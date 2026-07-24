"""Turn a single-file library workflow into a routine.

- `materialize`: the un-decomposed baseline — the whole workflow, rendered to markdown, becomes
  the routine's main.md. Used for sub-routines and as a fallback.
- `decompose`: the generator LLM applies the workflow to the initial instruction and splits it
  into the routine's entry (main.md) + one markdown STAGE per step/state of the workflow. Runs
  as a PIPELINE of scoped completions — outline → main → one call per stage → adapted traits —
  so no single completion carries the whole routine (the 2026-07-24 one-shot truncation shipped
  stageless/stub routines twice in one day). Falls back to `materialize` on failure.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import frontmatter

from ..ids import is_slug
from .library import head_commit, read_workflow


def dump_markdown(meta: dict, body: str) -> str:
    r"""Meta + markdown body → the main.md document: '---\n<yaml>\n---\n\n<body>' with key
    order preserved and exactly one trailing newline. The single writer-side counterpart of
    frontmatter.parse, so materialized files always round-trip.
    """
    post = frontmatter.Post(body.strip())
    post.metadata = dict(meta)
    return frontmatter.dumps(post, sort_keys=False) + "\n"


def _routine_frontmatter(meta: dict, slug: str, provenance: dict, adapted: str) -> dict:
    fm = {"name": meta.get("name", slug), "slug": meta.get("slug", slug),
          "materialized_from": provenance, "adapted": adapted}
    if meta.get("includes"):
        fm["includes"] = list(meta["includes"])
    if meta.get("tags"):
        fm["tags"] = list(meta["tags"])
    if meta.get("tools") is not None:
        fm["tools"] = meta["tools"]
    return fm


def materialize(home: Path, slug: str, *, today: str | None = None) -> tuple[str, dict]:
    """Single-file workflow → the routine's main.md content (whole workflow rendered to markdown).
    The Python pattern is rendered to markdown — the orchestrator acts it out.
    """
    from .pyworkflow import render_markdown

    meta, raw = read_workflow(home, slug)
    provenance = {"slug": slug, "commit": head_commit(home), "version": meta.get("version", 0)}
    adapted = today or date.today().isoformat()  # noqa: DTZ011 — a local-date stamp is the point
    return dump_markdown(_routine_frontmatter(meta, slug, provenance, adapted),
                         render_markdown(raw, meta)), provenance


log = logging.getLogger("rsched.adapt")

# Pipeline budgets: each completion carries ONE artifact (the outline, main, a single stage, or
# the adapted traits) — small enough that output truncation cannot ship a stub routine. Each
# call gets DECOMPOSE_ATTEMPTS tries (transport errors AND invalid payloads) before the whole
# pipeline degrades to the verbatim pattern.
DECOMPOSE_TIMEOUT_S = 300
DECOMPOSE_ATTEMPTS = 2
OUTLINE_MAX_TOKENS = 8000
MAIN_MAX_TOKENS = 16000
STAGE_MAX_TOKENS = 16000
TRAITS_MAX_TOKENS = 32000

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

TRAITS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["traits"],
    "properties": {"traits": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["slug", "body"],
        "properties": {"slug": {"type": "string", "description": "the trait's slug, unchanged"},
                       "body": {"type": "string",
                                "description": "the trait ADAPTED to this routine"}}}}},
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
it is the SEED you are compiling from, not a document the run can read."""

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

_TRAITS_PROMPT = """\
You are adapting practice modules (TRAITS) for a routine that was just generated.

INSTRUCTION (the task this routine runs):
---
{instruction}
---

The routine's main.md:
---
{main}
---

TRAITS (reusable practice modules this routine adopts; each will live as the routine's own file
`traits/<slug>.md`, read on demand):
{trait_docs}

Return "traits": one entry per trait above, same slug, with the SAME practice ADAPTED to this
routine — keep its rules, structure and heading line (`# trait: <name> — <summary>`), but make
wording and examples concrete to THIS task, and cut anything that cannot apply to it. Keep each
about as long as the original or shorter.

Return ONLY the JSON object {{"traits": [{{"slug", "body"}}, ...]}}."""


def _render_outline(outline: list[dict]) -> str:
    return "\n".join(f"- `stages/{s['name']}.md` — scope: {s['scope']} · inputs: {s['inputs']}"
                     f" · outputs: {s['outputs']}" for s in outline)


def _is_stub(body: str) -> bool:
    """The observed failure: a stage module of one thin line that a run cannot act from."""
    return len([ln for ln in body.strip().splitlines() if ln.strip()]) < 2


def _pipeline(endpoint, ref, raw: str, instruction: str, *, params: dict, pins: list[str],
              trait_bodies: dict[str, str], slug: str) -> dict:
    """Outline → main → one call per stage → adapted traits. Raises on any hard failure
    (the caller falls back to materialize); a failed trait adaptation degrades softly to
    verbatim library traits.
    """

    def complete(prompt: str, schema: dict, max_tokens: int, what: str, check=None):
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
            except Exception as exc:  # transport error OR invalid payload → same retry
                last = exc
                log.warning("decompose(%s) %s attempt %d/%d failed: %s", slug, what,
                            attempt, DECOMPOSE_ATTEMPTS, exc)
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

    outline = complete(context + _OUTLINE_TAIL + param_note + pin_note,
                       OUTLINE_SCHEMA, OUTLINE_MAX_TOKENS, "outline", check=check_outline)
    outline_txt = _render_outline(outline)

    standing = ""
    if trait_bodies:
        trait_lines = "\n".join(f"- {t}: {body.strip().splitlines()[0]}"
                                for t, body in trait_bodies.items())
        standing = ("\n\nEnd main with a `## Standing practices` section: one line per trait — "
                    "`- traits/<slug>.md — <when to read it during a run>` — for these traits "
                    "(adapted copies will exist as the routine's own files):\n" + trait_lines)

    def check_main(data: dict) -> str:
        main = str(data.get("main") or "").strip()
        if not main:
            raise ValueError("empty main")
        missing = [s["name"] for s in outline if f"stages/{s['name']}.md" not in main]
        if missing:
            raise ValueError(f"main.md does not route to stage(s): {missing}")
        return main

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
    for s in outline:
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

    adapted: dict[str, str] = {}
    if trait_bodies:
        try:
            docs = "\n\n".join(f"--- trait: {t} ---\n{body}"
                               for t, body in trait_bodies.items())
            tdata = complete(_TRAITS_PROMPT.format(instruction=instruction, main=main,
                                                   trait_docs=docs),
                             TRAITS_SCHEMA, TRAITS_MAX_TOKENS, "traits")
            adapted = {t["slug"]: str(t["body"]).strip() for t in (tdata.get("traits") or [])
                       if t.get("slug") in trait_bodies and str(t.get("body", "")).strip()}
        except Exception as exc:  # soft: the caller copies the library traits verbatim
            log.warning("decompose(%s) trait adaptation failed — library traits will be "
                        "copied verbatim: %s", slug, exc)
    return {"main": main, "stages": stages, "traits": adapted, "degraded": False}


def decompose(server, slug: str, instruction: str, *, params: dict | None = None,
              traits: list[str] | None = None) -> dict:
    """Generator LLM: apply a single-file workflow to `instruction` and split it into the
    routine's main.md body + stage/state modules, ADAPTING the selected traits along the way.
    Returns {'main': <body>, 'stages': {name: body}, 'traits': {slug: adapted body},
    'degraded': bool}.

    Runs as a pipeline of SCOPED completions (outline → main → one per stage → traits), each
    retried DECOMPOSE_ATTEMPTS times over transport errors and invalid payloads — the one-shot
    design shipped stub routines whenever its single huge completion truncated (D41,
    2026-07-24). Any hard failure degrades to the whole workflow rendered as main.md with
    `degraded` True so callers can SAY so.
    """
    from .. import library_docs

    meta, raw = read_workflow(server.libraries_home, slug)
    # A pattern may PIN deliverable paths (META["pin"]: str | list) that MUST survive
    # decomposition — the tailored files must still name them. The observed failure mode:
    # applied to a draft that itself describes a routine (the wizard's clarify-instruction),
    # the generator sometimes builds THAT routine and silently drops the pattern's real
    # deliverable. A dropped pin falls back to the verbatim pattern, which always keeps it.
    pins = [meta["pin"]] if isinstance(meta.get("pin"), str) else list(meta.get("pin") or [])
    trait_bodies: dict[str, str] = {}
    for t in traits or []:
        raw_doc = library_docs.read_doc(server.traits_home, t)
        if raw_doc:
            trait_bodies[t] = library_docs.doc_body(raw_doc).strip()
    try:
        from ..endpoints import EndpointRegistry

        endpoint, ref = EndpointRegistry(server).for_system()
        return _pipeline(endpoint, ref, raw, instruction, params=params or {}, pins=pins,
                         trait_bodies=trait_bodies, slug=slug)
    except Exception as exc:
        # a stageless recipe is a real quality drop — the fallback must never be silent
        log.warning("decompose(%s) pipeline failed — materializing the whole pattern as "
                    "main.md", slug, exc_info=exc)
        from .pyworkflow import render_markdown
        return {"main": render_markdown(raw, meta), "stages": {}, "traits": {}, "degraded": True}
