"""The MATERIALIZATION pipeline — turning a workflow pattern plus an instruction into a recipe.

Split out of `adapt.py` (F393): `adapt` decides WHAT to materialize and where it lands; this is
the multi-step LLM pipeline that writes it.

This is also where the recipe rules are enforced at their only enforceable point. A recipe says
WHAT, never which tool — so the prompts here spell out the forbidden forms, because these
documents are LLM-written and the generator is the cause. A name-matching linter over a finished
file would go red the day a util is named after an ordinary word.
"""

# the prompts and schemas the pipeline writes with — including the recipe rules, which are enforced
# HERE at generation because a linter over a finished file cannot be.
from __future__ import annotations

import contextlib
import json
import logging

from ..ids import is_slug

# Pipeline budgets: each completion carries ONE artifact (the outline, main, or a single
# stage) — small enough that output truncation cannot ship a stub routine. Each
# call gets DECOMPOSE_ATTEMPTS tries (transport errors AND invalid payloads) before the whole
# pipeline degrades to the verbatim pattern.
DECOMPOSE_TIMEOUT_S = 300

DECOMPOSE_ATTEMPTS = 2

OUTLINE_MAX_TOKENS = 8000

MAIN_MAX_TOKENS = 16000

STAGE_MAX_TOKENS = 16000

OUTLINE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["stages"],
    "properties": {"stages": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "scope", "inputs", "outputs"],
        "properties": {
            "name": {"type": "string",
                     "description": "kebab-case stage/state name, specific to this task"},
            "scope": {"type": "string", "description": "what THIS stage alone covers"},
            "inputs": {"type": "string",
                       "description": "what it reads: state/ files, prior stages' outputs"},
            "outputs": {"type": "string",
                        "description": "the exact files/decisions it produces"}}}}},
}

MAIN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["main"],
    "properties": {"main": {"type": "string",
                            "description": "main.md body: the entry state-machine that routes "
                                           "into the stages"}},
}

STAGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["body"],
    "properties": {"body": {"type": "string",
                            "description": "the stage's complete markdown module"}},
}

_CONTEXT = """\
You are generating a ROUTINE by applying a workflow pattern to a specific task.

WORKFLOW (a Python control-flow pattern — a precise depiction you do NOT execute):
---
{workflow}
---

INSTRUCTION (the task this routine runs):
---
{instruction}
---

"""

_SELF_CONTAINED = """

SELF-CONTAINED — the running agent acts ONLY from main.md and the stage modules; the INSTRUCTION
above will NOT exist at run time. So INLINE every concrete detail the task needs directly into
them: exact values, thresholds, names, formats, category lists, file paths, URLs, output shapes,
completion criteria. Never write "as the instruction says" or otherwise defer to the instruction —
it is the SEED you are compiling from, not a document the run can read.

WHAT, NEVER WHICH TOOL — a recipe describes the WORK, never the toolbox. Never name a util, and
never write a util's flags or invocation. Name the CAPABILITY the step needs ("fetch the page",
"run the repo's test suite", "send the mail", "publish the site") and leave the choice of tool to
the run: it is shown the live tool catalog in its CAPABILITIES section, and it records what worked
in its own memory across runs. A tool named here is stale the day it is renamed or removed, and it
stops the run from discovering a better one. This does NOT soften the inlining rule above: TASK
facts (values, thresholds, paths, formats) belong in the recipe; TOOL choices never do.

INSTRUCTIONS, NEVER HISTORY — write only what the run must DO. The agent reading this recipe has
never seen an earlier draft of it, so a sentence contrasting the current design with a previous
one is invisible to it: it costs tokens every turn and teaches nothing, and "X was retired" or
"the old approach did Y" read as facts about the world it should act on. Forbidden in every line
you write: "no longer", "used to", "previously", "as of the last change", "was retired/replaced/
renamed", "nothing replaced it", "never reintroduce", "don't revert to", "the old/former/previous
<thing>", and dates or counts that were true once. State the current design in the present tense
and stop. Where a rule exists for a reason that matters, express the reason as a consequence of
following it now ("commit before the multi-file edit so you have an undo point"), never as the
history of the decision."""

_OUTLINE_TAIL = """\
Plan this routine's STAGE OUTLINE — the set of stage modules a fresh agent will work through in
order, one per step/state of the workflow, tailored to THIS task. Each stage will be generated
as its own module from this outline.

Return JSON {"stages": [{"name", "scope", "inputs", "outputs"}, ...]} with 3-8 entries:
- name: kebab-case, concrete and specific to THIS task — the filenames are the live progress
  diagram the user watches, so each must read as this task's real step (never a generic
  workflow or function name).
- scope: what this stage ALONE covers. Scopes must be MUTUALLY EXCLUSIVE and together cover the
  workflow's whole control flow — no step of the pattern may be missing, none owned twice.
- inputs: what the stage reads (state/ files, prior stages' outputs, external sources).
- outputs: the exact files/decisions it produces (state/ paths, deliverables)."""

_MAIN_RULES = """\
Write "main": the routine's ENTRY (main.md body). A state machine — it tells the run to read +
follow the current stage's module with read_file, working through the stages in order (the
engine derives the run's live position from those reads: reading `stages/<name>.md` marks the
run as IN that stage — no progress bookkeeping is needed). Durable state a FUTURE run needs (a
lifecycle marker, a cursor) lives in its own `state/` file the stages define. It keeps a
`## Run flow` section and a `## Completion criteria` section. `## Run flow` is a NUMBERED list;
every item LEADS with a **bold** stage name matching the stage filename and names its file as
`stages/<name>.md` — reference EVERY stage of the outline, in its order. Turn the pattern's
control flow into concrete prose for THIS task — never leave Python in the output.

Return ONLY the JSON object {"main": ...}."""

_STAGE_RULES = """\
- Cover exactly this stage's scope — the concrete procedure (what to read, decide, do, write
  and verify), the exact `state/` files and output shapes it touches, its edge cases, and what
  done looks like; typically 20-60 lines. A one-line summary or placeholder stub is a FAILURE —
  at run time the agent has NOTHING else to act from.
- Do NOT restate other stages' procedures — each scope is owned by its own module; end by
  routing the way main.md does (the next stage, or the run's close).
- Turn the pattern's step into concrete prose for THIS task — never leave Python in the output.

Return ONLY the JSON object {"body": ...}."""


log = logging.getLogger("rsched.workflows.pipeline")


def _render_outline(outline: list[dict]) -> str:
    return "\n".join(f"- `stages/{s['name']}.md` — scope: {s['scope']} · inputs: {s['inputs']}"
                     f" · outputs: {s['outputs']}" for s in outline)

def _is_stub(body: str) -> bool:
    """The observed failure: a stage module of one thin line that a run cannot act from."""
    return len([ln for ln in body.strip().splitlines() if ln.strip()]) < 2

def _pipeline(resolve, raw: str, instruction: str, *, params: dict, pins: list[str],
              rule_lines: str, slug: str, progress=None) -> dict:
    """Outline → main → one call per stage. Raises on any hard failure (the caller falls
    back to materialize). `progress(step: str, done: int, total: int)` — best-effort
    live reporting (F192: the creation surface shows WHICH step the build is on); total grows once
    the outline fixes the stage count.

    `resolve() -> (endpoint, ref)` is called for the initial pick AND again after every
    failed attempt: a hard endpoint failure (provider outage, spent credits) marks the
    model cooling in this process, so the re-pick lands on the chain's next not-cooling
    fallback instead of hammering the same dead endpoint for all attempts (F197 — the
    2026-07-24 credit outage shipped a stageless routine because every retry hit the
    same exhausted claude endpoint while the clarify RUN had failed over fine).
    """
    endpoint, ref = resolve()

    def report(step: str, done: int, total: int) -> None:
        if progress is not None:
            with contextlib.suppress(Exception):   # reporting must never break a build
                progress(step, done, total)

    def complete(prompt: str, schema: dict, max_tokens: int, what: str, check=None):
        nonlocal endpoint, ref
        last: Exception | None = None
        for attempt in range(1, DECOMPOSE_ATTEMPTS + 1):
            try:
                comp = endpoint.complete(
                    [{"role": "user", "content": prompt}], model=ref.model, schema=schema,
                    effort=ref.effort, temperature=ref.temperature,
                    max_tokens=max(int(ref.max_tokens or 0), max_tokens),
                    timeout=DECOMPOSE_TIMEOUT_S,
                    purpose=f"Decompose {what} → {slug}", kind="decompose")
                data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
                return check(data) if check else data
            except Exception as exc:  # transport error OR invalid payload → retry
                last = exc
                log.warning("decompose(%s) %s attempt %d/%d on %s failed: %s", slug, what,
                            attempt, DECOMPOSE_ATTEMPTS, getattr(ref, "model", "?"), exc)
                # F197: re-pick the chain — a hard-failed model is cooling now, so the next
                # attempt gets its first not-cooling fallback (call-time failover, like the
                # engine's turn completion; without this every attempt hits the dead model).
                with contextlib.suppress(Exception):
                    endpoint, ref = resolve()
        raise last or RuntimeError(f"decompose {what} failed")

    param_note = ("\n\nPARAMETERS (the pattern's contract, resolved with the user):\n"
                  + "\n".join(f"- {k}: {v}" for k, v in params.items())
                  + "\nBind each resolved VALUE inline into main and every stage that "
                    "uses it — these parameter NAMES will not exist at run time; prose "
                    "that defers to a parameter name instead of its concrete value is a "
                    "failure.") if params else ""
    pin_note = ("\n\nPINNED DELIVERABLES — the generated main/stages MUST keep these "
                "literal paths, serving the same role they have in the workflow "
                "pattern:\n" + "\n".join(f"- {p}" for p in pins)) if pins else ""
    context = _CONTEXT.format(workflow=raw, instruction=instruction)

    def check_outline(data: dict) -> list[dict]:
        seen: set[str] = set()
        outline = []
        for s in data.get("stages") or []:
            name = str(s.get("name", ""))
            if is_slug(name) and name not in seen and str(s.get("scope", "")).strip():
                seen.add(name)
                outline.append({"name": name, "scope": str(s.get("scope", "")).strip(),
                                "inputs": str(s.get("inputs", "")).strip(),
                                "outputs": str(s.get("outputs", "")).strip()})
        if not outline:
            raise ValueError("outline produced no usable stages")
        return outline

    report("planning the stage outline", 0, 3)
    outline = complete(context + _OUTLINE_TAIL + param_note + pin_note,
                       OUTLINE_SCHEMA, OUTLINE_MAX_TOKENS, "outline", check=check_outline)
    # total = outline + main + each stage
    total = 2 + len(outline)
    outline_txt = _render_outline(outline)

    standing = ""
    if rule_lines:
        standing = ("\n\nEnd main with a `## Standing practices` section: one line per rule — "
                    "`- <slug> — <when to read it during a run>` — for the general rules this "
                    "routine holds. They live in the shared library and the run reads one with "
                    "read_rule; do NOT restate or tailor their prose here:\n" + rule_lines)

    def check_main(data: dict) -> str:
        main = str(data.get("main") or "").strip()
        if not main:
            raise ValueError("empty main")
        missing = [s["name"] for s in outline if f"stages/{s['name']}.md" not in main]
        if missing:
            raise ValueError(f"main.md does not route to stage(s): {missing}")
        return main

    report("writing main.md (the entry state machine)", 1, total)
    main = complete(context + "The routine's stages are already planned — the OUTLINE (each "
                    "stage is generated as its own module):\n" + outline_txt + "\n\n"
                    + _MAIN_RULES + standing + _SELF_CONTAINED + param_note + pin_note,
                    MAIN_SCHEMA, MAIN_MAX_TOKENS, "main", check=check_main)

    def check_stage(data: dict) -> str:
        body = str(data.get("body") or "").strip()
        if _is_stub(body):
            raise ValueError("stage came back as a one-line stub")
        return body

    stages: dict[str, str] = {}
    for k, s in enumerate(outline):
        report(f"writing stage {k + 1}/{len(outline)}: {s['name']}", 2 + k, total)
        prompt = (context + "The routine's main.md (already generated):\n---\n" + main
                  + "\n---\n\nThe full stage OUTLINE (each stage is its own module):\n"
                  + outline_txt + "\n\nWrite the COMPLETE module for the stage "
                  + f"`{s['name']}` (file `stages/{s['name']}.md`) and ONLY that stage.\n"
                  + f"- Its scope: {s['scope']}\n- Its inputs: {s['inputs']}\n"
                  + f"- Its outputs: {s['outputs']}\n" + _STAGE_RULES
                  + _SELF_CONTAINED + param_note + pin_note)
        stages[s["name"]] = complete(prompt, STAGE_SCHEMA, STAGE_MAX_TOKENS,
                                     f"stage {s['name']}", check=check_stage)

    missing = [p for p in pins if p not in main and not any(p in b for b in stages.values())]
    if missing:
        raise ValueError(f"decompose dropped pinned deliverable(s): {missing}")

    return {"main": main, "stages": stages, "degraded": False}
