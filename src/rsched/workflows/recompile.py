"""Recompile a routine: re-derive main.md + the decompose-generated steps/ from the CURRENT
instruction (the seed) applied to its source workflow — the same operation scaffold runs at
creation, on demand from the routine page.

Deliberately narrow:
- traits/ are NOT re-adapted — they are the routine's own, improver-refined files ('never toggled').
- step modules the routine did NOT get from decompose (wizard extras, hand-added) are preserved;
  only the previously generated ones are replaced.
- a degraded decompose (the model call failed → no modules) never silently flattens a routine that
  HAD steps: it raises so the existing steps are left untouched.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter

from .. import library_docs
from . import library, provenance
from .adapt import decompose, dump_markdown
from .scaffold import _with_practices_tail


def recompile_routine(server, routine_dir: Path, cfg) -> dict:
    """Rewrite main.md + steps/ from `routine_dir`'s instruction × its workflow. Returns
    {'modules': [...], 'removed': [...]}. Raises ValueError if the routine has no source workflow
    (hand-authored) or that workflow is gone from the library; RuntimeError on a degraded decompose."""
    if not cfg.workflow_slug:
        raise ValueError("this routine was written directly (no source workflow) — edit main.md/steps")
    try:
        meta, _, _ = library.read_workflow(server.library_home, cfg.workflow_slug)
    except FileNotFoundError as exc:
        raise ValueError(f"workflow {cfg.workflow_slug!r} is no longer in this library") from exc

    instruction = (routine_dir / "instruction.md").read_text(encoding="utf-8") \
        if (routine_dir / "instruction.md").exists() else ""
    main_path = routine_dir / "main.md"
    old_meta: dict = {}
    if main_path.exists():
        old_meta, _ = frontmatter.parse(main_path.read_text(encoding="utf-8"))
    old_modules = [m for m in (old_meta.get("modules") or []) if isinstance(m, str)]
    traits_dir = routine_dir / "traits"
    traits = sorted(p.stem for p in traits_dir.glob("*.md")) if traits_dir.is_dir() else []

    result = decompose(server, cfg.workflow_slug, instruction, traits=traits)
    modules = result["modules"]
    if old_modules and not modules:
        # decompose fell back to materialize (no endpoint / model error) — refuse to flatten a
        # routine that had real step modules; leave every file as-is.
        raise RuntimeError("recompile produced no step modules — the model call likely failed; "
                           "steps were left unchanged")

    steps_dir = routine_dir / "steps"
    steps_dir.mkdir(exist_ok=True)
    removed = [m for m in old_modules if m not in modules]
    for name in removed:                       # drop only the PREVIOUSLY generated modules
        (steps_dir / f"{name}.md").unlink(missing_ok=True)
    for name, body in modules.items():
        (steps_dir / f"{name}.md").write_text(body.rstrip() + "\n", encoding="utf-8")

    # Standing-practices tail from the routine's EXISTING (own) traits — never re-adapted here.
    trait_summaries: dict[str, str] = {}
    for t in traits:
        raw = (traits_dir / f"{t}.md").read_text(encoding="utf-8")
        m = library_docs.DOC_RE.search(raw)
        trait_summaries[t] = m.group("summary").strip() if m else ""
    main_body = _with_practices_tail(result["main"], trait_summaries)

    main_meta = {
        "name": old_meta.get("name", cfg.name),
        "slug": old_meta.get("slug", cfg.slug),
        "materialized_from": {"slug": cfg.workflow_slug,
                              "commit": library.head_commit(server.library_home),
                              "version": meta.get("version", 0)},
        "modules": sorted(modules),
        # the workflow's current tools allowlist wins (recompiling = re-adopting the pattern)
        **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {}),
        **({"tags": list(old_meta["tags"])} if old_meta.get("tags") else {}),
        **({"includes": list(old_meta["includes"])} if old_meta.get("includes") else {}),
    }
    main_path.write_text(
        dump_markdown(provenance.stamp(main_meta, routine_dir=routine_dir,
                                       main_body=main_body, instruction=instruction), main_body),
        encoding="utf-8")
    return {"modules": sorted(modules), "removed": removed}
