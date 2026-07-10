"""Turn a single-file library workflow into a routine.

- `materialize`: the un-decomposed baseline — the whole workflow becomes the routine's main.md
  (fill {{params}}, stamp provenance). Used for sub-routines and as a fallback.
- `decompose`: the generator LLM applies the workflow to the initial instruction and splits it
  into the routine's entry (main.md) + one markdown MODULE per step/state of the workflow. This is
  what makes a user-created routine a set of modular files. Falls back to `materialize` on failure.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .. import frontmatter
from ..ids import is_slug
from .library import head_commit, read_workflow


def fill_params(text: str, params: dict | None) -> str:
    for key, val in (params or {}).items():
        text = text.replace("{{" + key + "}}", str(val))
    return text


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


def materialize(home: Path, slug: str, *, params: dict | None = None,
                today: str | None = None) -> tuple[str, dict]:
    """Single-file workflow → the routine's main.md content (whole workflow, provenance stamped).
    A Python pattern is rendered to markdown (the orchestrator acts it out); a legacy markdown
    workflow has its {{params}} filled. Raises KeyError for missing markdown params."""
    params = params or {}
    meta, body, raw = read_workflow(home, slug)
    provenance = {"slug": slug, "commit": head_commit(home), "version": meta.get("version", 0)}
    adapted = today or date.today().isoformat()
    if meta.get("format") == "py":
        from .pyworkflow import render_markdown
        return frontmatter.dump(_routine_frontmatter(meta, slug, provenance, adapted),
                                render_markdown(raw, meta)), provenance
    declared = meta.get("params") or []
    missing = [p for p in declared if p not in params]
    if missing:
        raise KeyError(f"workflow {slug!r} requires params: {missing}")
    body = fill_params(body, params)
    out_meta = dict(meta)
    out_meta.pop("params", None)
    out_meta.pop("format", None)
    out_meta["materialized_from"] = provenance
    out_meta["adapted"] = adapted
    return frontmatter.dump(out_meta, body), provenance


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

WORKFLOW ({kind}):
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

If the workflow is a Python pattern, turn each of its steps (the `main()` control flow and the
functions it calls) into concrete prose for THIS task — never leave Python in the output.

Return ONLY the JSON object {{main, modules}}."""


def decompose(server, slug: str, instruction: str, *, params: dict | None = None) -> dict:
    """Generator LLM: apply a single-file workflow to `instruction` and split it into the routine's
    main.md body + step/state modules. Returns {'main': <body>, 'modules': {name: body}}. Degrades
    to the whole workflow rendered as main.md (no modules) on any failure — so generation without a
    usable endpoint still yields a valid, self-contained markdown routine."""
    meta, body, raw = read_workflow(server.library_home, slug)
    is_py = meta.get("format") == "py"
    pattern = raw if is_py else fill_params(body, params)
    try:
        from ..endpoints import EndpointRegistry

        endpoint, ref = EndpointRegistry(server).for_system()
        kind = ("a Python control-flow pattern (a precise depiction you do NOT execute)" if is_py
                else "a markdown process pattern")
        param_note = ("\n\nPARAMETERS (the pattern's contract, resolved with the user):\n"
                      + "\n".join(f"- {k}: {v}" for k, v in params.items())) if params else ""
        prompt = _DECOMPOSE_PROMPT.format(kind=kind, workflow=pattern, instruction=instruction) + param_note
        comp = endpoint.complete([{"role": "user", "content": prompt}], model=ref.model,
                                 schema=DECOMPOSE_SCHEMA, effort=ref.effort, timeout=180)
        data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
        modules = {m["name"]: m["body"] for m in (data.get("modules") or [])
                   if is_slug(str(m.get("name", ""))) and str(m.get("body", "")).strip()}
        main = str(data.get("main") or "").strip()
        if not main:
            raise ValueError("empty main")
        return {"main": main, "modules": modules}
    except Exception:
        if is_py:
            from .pyworkflow import render_markdown
            return {"main": render_markdown(raw, meta), "modules": {}}
        return {"main": pattern, "modules": {}}
