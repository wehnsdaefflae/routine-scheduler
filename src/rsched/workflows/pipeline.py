"""The MATERIALIZATION pipeline — turning a workflow pattern plus an instruction into a recipe.

Split out of `adapt.py` (F393): `adapt` decides WHAT to materialize and where it lands; this is
the multi-step LLM pipeline that writes it.

Its prompts live in `pipeline_prompts.py`, where the recipe rules are enforced at their only
enforceable point. The one rule with a fixed vocabulary — a contract names only the task
(`taskcontract`) — is ALSO checked on every main and stage this pipeline gets back: a reply that
fails it is an invalid payload, retried like a malformed one, so a recipe that asks its run for
bookkeeping cannot be written by this path at all.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re

from ..ids import is_slug
from . import taskcontract
from .pipeline_prompts import CONTEXT, MAIN_RULES, OUTLINE_TAIL, SELF_CONTAINED, STAGE_RULES

# Pipeline budgets: each completion carries ONE artifact (the outline, main, or a single
# stage) — small enough that output truncation cannot ship a stub routine. Each
# call gets DECOMPOSE_ATTEMPTS tries (transport errors AND invalid payloads) before the whole
# pipeline degrades to the verbatim pattern.
# These are CEILINGS, not requests: `_budget` lowers each to what the resolved model can
# actually return, because a model asked above its own output cap answers a 400 or truncates.
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

log = logging.getLogger("rsched.workflows.pipeline")


def _render_outline(outline: list[dict]) -> str:
    return "\n".join(f"- `stages/{s['name']}.md` — scope: {s['scope']} · inputs: {s['inputs']}"
                     f" · outputs: {s['outputs']}" for s in outline)

def _is_stub(body: str) -> bool:
    """The observed failure: a stage module of one thin line that a run cannot act from."""
    return len([ln for ln in body.strip().splitlines() if ln.strip()]) < 2

def _hold_contract(text: str, done_when: list[tuple[str, str]]) -> None:
    """Refuse a generated main/stage that asks its run for bookkeeping (`taskcontract`)."""
    if found := [*taskcontract.done_when_findings(done_when),
                 *taskcontract.ledger_findings(text)]:
        raise ValueError("; ".join(found))

def _outcome_prompt(outcome) -> str:
    """What the routine exists to produce (engine/outcome_words.py), told to the main call."""
    if outcome is None:
        return ""
    from ..engine.outcome_words import done_when_line

    rows = "\n".join(f"- `{m.id}` ({m.kind}, {m.better} is better) — {m.label}"
                     for m in outcome.measures)
    return (f"\n\nWhat this routine exists to PRODUCE for {outcome.principal} — its outcome, "
            "counted after every run by its own `scripts/outcome.py`:\n" + rows + "\nAdd this "
            f"line to `## Done when`, with its d-number: `- dN — {done_when_line(outcome)}`. "
            "Make the stages keep, in `state/`, the dated records each measure is counted "
            "from, and say in main that a run which finds no `scripts/outcome.py` writes it: "
            "standard library only, reading only this routine's own records, printing ONE "
            "JSON object of those ids and their numbers on its last line, with a "
            "`--selftest`. A measure the records cannot support is left out, never estimated.")


def _budget(ref, want: int) -> int:
    """How many output tokens ONE call asks for: the artifact's constant, never more than this
    model can return. `ModelRef.max_tokens` is that cap already resolved (per-MODEL config →
    the provider-discovered maximum → the endpoint default → the engine floor), so asking above
    it is asking for a 400 or a truncation — providers validate `input + requested_output <=
    window` (endpoints/limits.py). An UNRESOLVED cap (0/None, a model nothing was discovered
    for) leaves the constant standing: clamping to it would ask for nothing at all.
    """
    cap = int(ref.max_tokens or 0)
    return min(want, cap) if cap else want


def _pipeline(resolve, raw: str, instruction: str, *, pins: list[str],
              done_when: list[str], never: list[str], slug: str, outcome=None) -> dict:
    """Outline → main → one call per stage. Raises on any hard failure (the caller falls
    back to materialize).

    `resolve() -> (endpoint, ref)` is called for the initial pick AND again after every
    failed attempt: a hard endpoint failure (provider outage, spent credits) marks the
    model cooling in this process, so the re-pick lands on the chain's next not-cooling
    fallback instead of hammering the same dead endpoint for all attempts (F197 — the
    2026-07-24 credit outage shipped a stageless routine because every retry hit the
    same exhausted claude endpoint while the clarify RUN had failed over fine).
    """
    endpoint, ref = resolve()

    def complete(prompt: str, schema: dict, max_tokens: int, what: str, check=None):
        nonlocal endpoint, ref
        last: Exception | None = None
        for attempt in range(1, DECOMPOSE_ATTEMPTS + 1):
            try:
                comp = endpoint.complete(
                    [{"role": "user", "content": prompt}], model=ref.model, schema=schema,
                    effort=ref.effort, temperature=ref.temperature,
                    max_tokens=_budget(ref, max_tokens),
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

    pin_note = ("\n\nPINNED DELIVERABLES — the generated main/stages MUST keep these "
                "literal paths, serving the same role they have in the workflow "
                "pattern:\n" + "\n".join(f"- {p}" for p in pins)) if pins else ""
    context = CONTEXT.format(workflow=raw, instruction=instruction)

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

    outline = complete(context + OUTLINE_TAIL + pin_note,
                       OUTLINE_SCHEMA, OUTLINE_MAX_TOKENS, "outline", check=check_outline)
    outline_txt = _render_outline(outline)

    users_done = ("\n\nWhat the USER said a finished run delivers — carry each into `## Done "
                  "when` in their words, merged with the pattern's skeleton:\n"
                  + "\n".join(f"- {line}" for line in done_when)) if done_when else ""
    users_never = ("\n\nWhat the USER said a run must NEVER do — end main with a `## Never` "
                   "section after `## Done when`, one `- <prohibition>` line per item, in "
                   "their words:\n" + "\n".join(f"- {line}" for line in never)) if never else ""

    users_outcome = _outcome_prompt(outcome)

    def check_main(data: dict) -> str:
        main = str(data.get("main") or "").strip()
        if not main:
            raise ValueError("empty main")
        missing = [s["name"] for s in outline if f"stages/{s['name']}.md" not in main]
        if missing:
            raise ValueError(f"main.md does not route to stage(s): {missing}")
        # `## Done when` is checked because the run is held to it line by line at every
        # finish: a malformed line is a line no finish can account for. A machine check is
        # right here (unlike a util-name check over dynamic prose) — the format is fixed and
        # the generator is told to emit exactly it.
        from ..engine import donewhen

        if not donewhen.parse(main):
            raise ValueError("main.md has no `## Done when` section with a d1 line")
        if found := donewhen.problems(main, {s["name"] for s in outline}):
            raise ValueError("; ".join(found))
        _hold_contract(main, [(ln["id"], ln["text"]) for ln in donewhen.parse(main)])
        if never and not re.search(r"^## Never\s*$", main, re.MULTILINE):
            raise ValueError("main.md has no `## Never` section for what the user prohibited")
        from ..readmodels.surface_outcome import names_a_measure

        if outcome and not any(names_a_measure(line.get("text", ""),
                                               [m.id for m in outcome.measures])
                               for line in donewhen.parse(main)):
            raise ValueError("no `## Done when` line names an outcome measure")
        return main

    main = complete(context + "The routine's stages are already planned — the OUTLINE (each "
                    "stage is generated as its own module):\n" + outline_txt + "\n\n"
                    + MAIN_RULES + users_done + users_never + users_outcome
                    + SELF_CONTAINED + pin_note,
                    MAIN_SCHEMA, MAIN_MAX_TOKENS, "main", check=check_main)

    def check_stage(data: dict) -> str:
        body = str(data.get("body") or "").strip()
        if _is_stub(body):
            raise ValueError("stage came back as a one-line stub")
        _hold_contract(body, [])
        return body

    stages: dict[str, str] = {}
    for s in outline:
        prompt = (context + "The routine's main.md (already generated):\n---\n" + main
                  + "\n---\n\nThe full stage OUTLINE (each stage is its own module):\n"
                  + outline_txt + "\n\nWrite the COMPLETE module for the stage "
                  + f"`{s['name']}` (file `stages/{s['name']}.md`) and ONLY that stage.\n"
                  + f"- Its scope: {s['scope']}\n- Its inputs: {s['inputs']}\n"
                  + f"- Its outputs: {s['outputs']}\n" + STAGE_RULES
                  + SELF_CONTAINED + pin_note)
        stages[s["name"]] = complete(prompt, STAGE_SCHEMA, STAGE_MAX_TOKENS,
                                     f"stage {s['name']}", check=check_stage)

    missing = [p for p in pins if p not in main and not any(p in b for b in stages.values())]
    if missing:
        raise ValueError(f"decompose dropped pinned deliverable(s): {missing}")

    return {"main": main, "stages": stages, "degraded": False}
