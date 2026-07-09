"""Materialize a recipe (workflows/<slug>/main.md + steps/) into a routine's own main.md:
fill {{params}}, stamp provenance. Step modules and fragments stay as SEPARATE files (scaffold
copies them into the routine); for an ephemeral sub-workflow — which has no persistent routine
dir to hold module files — the modules can instead be inlined into the body."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .. import frontmatter
from .library import head_commit, list_modules, read_module, read_workflow


def fill_params(text: str, params: dict | None) -> str:
    for key, val in (params or {}).items():
        text = text.replace("{{" + key + "}}", str(val))
    return text


def materialize(home: Path, slug: str, *, params: dict | None = None,
                inline_modules: bool = False, today: str | None = None) -> tuple[str, dict]:
    """Returns (main.md content, provenance dict). Raises KeyError for missing params.

    inline_modules=True appends each step module into the body (a self-contained flow for a
    sub-workflow that has nowhere to read separate module files from)."""
    params = params or {}
    meta, body, _ = read_workflow(home, slug)          # the recipe's main.md
    declared = meta.get("params") or []
    missing = [p for p in declared if p not in params]
    if missing:
        raise KeyError(f"recipe {slug!r} requires params: {missing}")
    body = fill_params(body, params)
    commit = head_commit(home)
    if inline_modules:
        for mod in list_modules(home, slug):
            content = fill_params((read_module(home, slug, mod) or "").strip(), params)
            body = body.rstrip() + f"\n\n---\n### module: {mod}\n\n{content}\n"
    provenance = {"slug": slug, "commit": commit, "version": meta.get("version", 0)}
    out_meta = dict(meta)                                # carry the recipe's frontmatter forward
    out_meta.pop("params", None)                         # params are now filled in
    out_meta["materialized_from"] = provenance
    out_meta["adapted"] = today or date.today().isoformat()
    return frontmatter.dump(out_meta, body), provenance
