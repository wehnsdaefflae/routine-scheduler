"""Materialize a library workflow into a routine's workflow.md: fill params, inline the
fragments the routine's self-flags keep, record provenance."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .. import frontmatter
from .library import head_commit, read_fragment, read_workflow

# fragment slug → the routine.yaml self-flag that controls it (absent = always included)
FRAGMENT_FLAGS = {
    "self-audit": "audit",
    "improvement": "improve",
    "ledger-discipline": "ledger",
    "fresh-eyes": "fresh_eyes",
    "hygiene": "hygiene",
}


def materialize(home: Path, slug: str, *, params: dict | None = None,
                self_flags: dict | None = None, today: str | None = None) -> tuple[str, dict]:
    """Returns (workflow.md content, provenance dict). Raises KeyError for missing params."""
    params = params or {}
    self_flags = self_flags or {}
    meta, body, _ = read_workflow(home, slug)
    declared = meta.get("params") or []
    missing = [p for p in declared if p not in params]
    if missing:
        raise KeyError(f"workflow {slug!r} requires params: {missing}")
    for key, val in params.items():
        body = body.replace("{{" + key + "}}", str(val))

    commit = head_commit(home)
    included = []
    for frag in meta.get("includes") or []:
        flag = FRAGMENT_FLAGS.get(frag)
        if flag is not None and self_flags.get(flag, True) is False:
            continue
        included.append((frag, read_fragment(home, frag).strip()))
    if included:
        parts = ["## Standard practices",
                 "The following standing practices apply to every run (they are steps of the "
                 "run flow wherever it references them):"]
        for frag, text in included:
            parts.append(f"### fragment: {frag} @ {commit}\n\n{text}")
        body = body.rstrip() + "\n\n" + "\n\n".join(parts) + "\n"

    provenance = {"slug": slug, "commit": commit, "version": meta.get("version", 0)}
    out_meta = {
        "materialized_from": provenance,
        "adapted": today or date.today().isoformat(),
        "params": params,
    }
    return frontmatter.dump(out_meta, body), provenance
