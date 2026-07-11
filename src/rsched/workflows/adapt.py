"""Turn a single-file library workflow into a routine.

- `materialize`: the un-decomposed baseline — the whole workflow, rendered to markdown, becomes
  the routine's main.md (provenance stamped). Used for sub-routines and as a fallback.
- `decompose`: the generator LLM applies the workflow to the initial instruction and splits it
  into the routine's entry (main.md) + one markdown MODULE per step/state of the workflow. This is
  what makes a user-created routine a set of modular files. Falls back to `materialize` on failure.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import frontmatter

from ..ids import is_slug
from .library import head_commit, read_workflow

# One `### improve-<lens>` section: its heading line plus everything up to the next heading.
_IMPROVE_SECTION_RE = re.compile(r"(?m)^#{2,4}\s*`?improve-([a-z-]+)`?\b.*\n(?:(?!#{1,4}\s).*\n?)*")


def strip_inactive_improve(text: str, active: list[str] | set[str]) -> str:
    """Deterministic belt-and-braces after the generator LLM: drop `### improve-<lens>`
    sections whose fragment is NOT active for this routine. The decompose prompt asks the
    model to omit them, but LLM output isn't guaranteed — and a fragment toggled off later
    must take its step prose with it (see api_routines.set_fragments)."""
    active = set(active)
    return _IMPROVE_SECTION_RE.sub(
        lambda m: m.group(0) if f"improve-{m.group(1)}" in active else "", text)


def dump_markdown(meta: dict, body: str) -> str:
    """meta + markdown body → the main.md document: '---\\n<yaml>\\n---\\n\\n<body>' with key
    order preserved and exactly one trailing newline. The single writer-side counterpart of
    frontmatter.parse, so materialized files always round-trip."""
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
    """Single-file workflow → the routine's main.md content (whole workflow, provenance stamped).
    The Python pattern is rendered to markdown — the orchestrator acts it out."""
    from .pyworkflow import render_markdown

    meta, _, raw = read_workflow(home, slug)
    provenance = {"slug": slug, "commit": head_commit(home), "version": meta.get("version", 0)}
    adapted = today or date.today().isoformat()
    return dump_markdown(_routine_frontmatter(meta, slug, provenance, adapted),
                         render_markdown(raw, meta)), provenance


DECOMPOSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["main", "modules"],
    "properties": {
        "main": {"type": "string",
                 "description": "main.md body: the entry state-machine that routes into the modules"},
        "modules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "body"],
            "properties": {"name": {"type": "string", "description": "kebab-case module/state name"},
                           "body": {"type": "string", "description": "the module's markdown body"}}}},
    },
}

_DECOMPOSE_PROMPT = """You are generating a ROUTINE by applying a workflow pattern to a specific task.

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
- "main": the routine's ENTRY (main.md body). A state machine — it tells the run to read
  `state/phase.json`, then read + follow the current step's module with read_file. It names each
  module as `steps/<name>.md` and keeps `## Run flow` and `## Completion criteria` sections.
- "modules": one entry per step/state of the workflow (kebab-case `name` + markdown `body`),
  concrete and specific to THIS task. main.md must reference every module you create by its name.

Turn each of the pattern's steps (the `main()` control flow and the functions it calls) into
concrete prose for THIS task — never leave Python in the output.

Return ONLY the JSON object {{main, modules}}."""


def decompose(server, slug: str, instruction: str, *, params: dict | None = None,
              fragments: list[str] | None = None) -> dict:
    """Generator LLM: apply a single-file workflow to `instruction` and split it into the routine's
    main.md body + step/state modules. Returns {'main': <body>, 'modules': {name: body}}. Degrades
    to the whole workflow rendered as main.md (no modules) on any failure — so generation without a
    usable endpoint still yields a valid, self-contained markdown routine.

    `fragments` is the routine's ACTIVE standards list: the pattern may enumerate optional
    standards (the improve-* passes), and without knowing the active set the generator renders
    them all — the improve-ui leak. The prompt states the set, and the output is deterministically
    stripped of inactive improve sections afterwards."""
    meta, _, raw = read_workflow(server.library_home, slug)
    try:
        from ..endpoints import EndpointRegistry

        endpoint, ref = EndpointRegistry(server).for_system()
        param_note = ("\n\nPARAMETERS (the pattern's contract, resolved with the user):\n"
                      + "\n".join(f"- {k}: {v}" for k, v in params.items())) if params else ""
        frag_note = ("\n\nACTIVE FRAGMENTS (this routine's standards): "
                     + (", ".join(fragments) or "(none)")
                     + ". The pattern may name optional standards (e.g. the improve-* passes) — "
                       "write steps/sections ONLY for the active ones; omit inactive passes "
                       "entirely.") if fragments is not None else ""
        prompt = _DECOMPOSE_PROMPT.format(workflow=raw, instruction=instruction) \
            + param_note + frag_note
        comp = endpoint.complete([{"role": "user", "content": prompt}], model=ref.model,
                                 schema=DECOMPOSE_SCHEMA, effort=ref.effort, timeout=180)
        data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
        modules = {m["name"]: m["body"] for m in (data.get("modules") or [])
                   if is_slug(str(m.get("name", ""))) and str(m.get("body", "")).strip()}
        main = str(data.get("main") or "").strip()
        if not main:
            raise ValueError("empty main")
        if fragments is not None:
            main = strip_inactive_improve(main, fragments)
            modules = {k: strip_inactive_improve(v, fragments) for k, v in modules.items()}
        return {"main": main, "modules": modules}
    except Exception:
        from .pyworkflow import render_markdown
        return {"main": render_markdown(raw, meta), "modules": {}}
