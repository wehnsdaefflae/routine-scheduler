"""Turn a single-file library workflow into a routine.

- `materialize`: the un-decomposed baseline — the whole workflow, rendered to markdown, becomes
  the routine's main.md. Used for sub-routines and as a fallback.
- `decompose`: the generator LLM applies the workflow to the initial instruction and splits it
  into the routine's entry (main.md) + one markdown STAGE per step/state of the workflow. This is
  what makes a user-created routine a set of modular files. Falls back to `materialize` on failure.
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

# D41 generation budget: the decompose completion carries main + every stage + the adapted
# traits in ONE JSON object; give it output headroom and time, and try twice before degrading
# (observed 2026-07-24: two same-day wizard creations both shipped stageless fallbacks).
DECOMPOSE_MAX_TOKENS = 32000
DECOMPOSE_TIMEOUT_S = 300
DECOMPOSE_ATTEMPTS = 2

DECOMPOSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["main", "stages"],
    "properties": {
        "main": {"type": "string",
                 "description":
                     "main.md body: the entry state-machine that routes into the stages"},
        "stages": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "body"],
            "properties": {"name": {"type": "string",
                                    "description": "kebab-case stage/state name"},
                           "body": {"type": "string",
                                    "description": "the stage's markdown body"}}}},
        "traits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["slug", "body"],
            "properties": {"slug": {"type": "string",
                                    "description": "the trait's slug, unchanged"},
                           "body": {"type": "string",
                                    "description": "the trait ADAPTED to this routine"}}}},
    },
}

_DECOMPOSE_PROMPT = """\
You are generating a ROUTINE by applying a workflow pattern to a specific task.

WORKFLOW (a Python control-flow pattern — a precise depiction you do NOT execute):
---
{workflow}
---

INSTRUCTION (the task this routine runs):
---
{instruction}
---

Translate the workflow's control flow, tailored to the instruction, into markdown files a fresh
agent will follow one action per turn:
- "main": the routine's ENTRY (main.md body). A state machine — it tells the run to read + follow
  the current stage's module with read_file, working through the stages in order (the engine
  derives the run's live position from those reads: reading `stages/<name>.md` marks the run as IN
  that stage — no progress bookkeeping is needed). Durable state a FUTURE run needs (a lifecycle
  marker, a cursor) lives in its own `state/` file the stages define. It keeps a `## Run flow`
  section and a `## Completion criteria` section. `## Run flow` is a NUMBERED list; every item
  LEADS with a **bold** stage name matching the stage filename and names its file as
  `stages/<name>.md` — the UI's progress diagram shows the stage modules in the order main.md
  first mentions them.
- "stages": one entry per step/state of the workflow (kebab-case `name` + markdown `body`),
  concrete and specific to THIS task. The FILENAMES are the live progress diagram the user
  watches, so each must read as this task's real step (never a generic workflow or function
  name). main.md must reference every stage you create by its `stages/<name>.md` path, in order.
  Each stage body is a COMPLETE, self-sufficient module for that step — the concrete procedure
  (what to read, decide, do, write and verify), the exact `state/` files and output shapes it
  touches, its edge cases, and what done looks like; typically 20-60 lines. A one-line summary
  or placeholder stub is a FAILURE — at run time the agent has NOTHING else to act from.

Turn each of the pattern's steps (the `main()` control flow and the functions it calls) into
concrete prose for THIS task — never leave Python in the output.

SELF-CONTAINED — the running agent acts ONLY from main.md and the stage modules; the INSTRUCTION
above will NOT exist at run time. So INLINE every concrete detail the task needs directly into
them: exact values, thresholds, names, formats, category lists, file paths, URLs, output shapes,
completion criteria. Never write "as the instruction says" or otherwise defer to the instruction —
it is the SEED you are compiling from, not a document the run can read.

Return ONLY the JSON object {{main, stages}}."""

_TRAITS_NOTE = """

TRAITS (reusable practice modules this routine adopts; each will live as the routine's own file
`traits/<slug>.md`, read on demand):
{trait_docs}

Also return "traits": one entry per trait above, same slug, with the SAME practice ADAPTED to this
routine — keep its rules, structure and heading line (`# trait: <name> — <summary>`), but make
wording and examples concrete to THIS task, and cut anything that cannot apply to it. Keep each
about as long as the original or shorter.

End "main" with a `## Standing practices` section: one line per trait —
`- traits/<slug>.md — <when to read it during a run>`. Do NOT copy trait
text into main or the stages — reference the files."""


def decompose(server, slug: str, instruction: str, *, params: dict | None = None,
              traits: list[str] | None = None) -> dict:
    """Generator LLM: apply a single-file workflow to `instruction` and split it into the routine's
    main.md body + stage/state modules, ADAPTING the selected traits to the task along the way.
    Returns {'main': <body>, 'stages': {name: body}, 'traits': {slug: adapted body},
    'degraded': bool}. Tries twice (D41: the one-shot completion is big — main + stages + adapted
    traits — and a truncated/timed-out attempt used to silently ship a stageless routine), then
    degrades to the whole workflow rendered as main.md with `degraded` True so callers can SAY so.
    """
    from .. import library_docs

    meta, raw = read_workflow(server.libraries_home, slug)
    # A pattern may PIN deliverable paths (META["pin"]: str | list) that MUST survive
    # decomposition — the tailored files must still name them. The observed failure mode:
    # applied to a draft that itself describes a routine (the wizard's clarify-instruction),
    # the generator sometimes builds THAT routine and silently drops the pattern's real
    # deliverable. A dropped pin falls back to the verbatim pattern, which always keeps it.
    pins = [meta["pin"]] if isinstance(meta.get("pin"), str) else list(meta.get("pin") or [])
    trait_bodies = {}
    for t in traits or []:
        raw_doc = library_docs.read_doc(server.traits_home, t)
        if raw_doc:
            trait_bodies[t] = library_docs.doc_body(raw_doc).strip()
    last_exc: Exception | None = None
    for attempt in range(1, DECOMPOSE_ATTEMPTS + 1):
        try:
            from ..endpoints import EndpointRegistry

            endpoint, ref = EndpointRegistry(server).for_system()
            param_note = ("\n\nPARAMETERS (the pattern's contract, resolved with the user):\n"
                          + "\n".join(f"- {k}: {v}" for k, v in params.items())
                          + "\nBind each resolved VALUE inline into main and every stage that "
                            "uses it — these parameter NAMES will not exist at run time; prose "
                            "that defers to a parameter name instead of its concrete value is a "
                            "failure.") if params else ""
            trait_note = ""
            if trait_bodies:
                docs = "\n\n".join(f"--- trait: {t} ---\n{body}"
                                   for t, body in trait_bodies.items())
                trait_note = _TRAITS_NOTE.format(trait_docs=docs)
            pin_note = ("\n\nPINNED DELIVERABLES — the generated main/stages MUST keep these "
                        "literal paths, serving the same role they have in the workflow "
                        "pattern:\n" + "\n".join(f"- {p}" for p in pins)) if pins else ""
            prompt = _DECOMPOSE_PROMPT.format(workflow=raw, instruction=instruction) \
                + param_note + trait_note + pin_note
            # D41: the output is the whole routine in one JSON object — give it real headroom
            # (a catalog max_tokens tuned for chat replies truncates it, which is exactly the
            # observed stageless-scaffold failure) and a timeout that fits a long generation.
            comp = endpoint.complete([{"role": "user", "content": prompt}], model=ref.model,
                                     schema=DECOMPOSE_SCHEMA, effort=ref.effort,
                                     temperature=ref.temperature,
                                     max_tokens=max(int(ref.max_tokens or 0),
                                                    DECOMPOSE_MAX_TOKENS),
                                     timeout=DECOMPOSE_TIMEOUT_S,
                                     purpose=f"Decompose workflow → {slug}", kind="decompose")
            data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
            stages = {m["name"]: m["body"] for m in (data.get("stages") or [])
                      if is_slug(str(m.get("name", ""))) and str(m.get("body", "")).strip()}
            main = str(data.get("main") or "").strip()
            if not main:
                raise ValueError("empty main")
            missing = [p for p in pins
                       if p not in main and not any(p in b for b in stages.values())]
            if missing:
                raise ValueError(f"decompose dropped pinned deliverable(s): {missing}")
            adapted = {t["slug"]: str(t["body"]).strip() for t in (data.get("traits") or [])
                       if t.get("slug") in trait_bodies and str(t.get("body", "")).strip()}
            return {"main": main, "stages": stages, "traits": adapted, "degraded": False}
        except Exception as exc:  # any failure means: try again, then degrade loudly
            last_exc = exc
            log.warning("decompose(%s) attempt %d/%d failed: %s", slug, attempt,
                        DECOMPOSE_ATTEMPTS, exc)
    # a stageless recipe is a real quality drop — the fallback must never be silent
    log.warning("decompose(%s) failed after %d attempts — materializing the whole pattern "
                "as main.md", slug, DECOMPOSE_ATTEMPTS, exc_info=last_exc)
    from .pyworkflow import render_markdown
    return {"main": render_markdown(raw, meta), "stages": {}, "traits": {}, "degraded": True}
