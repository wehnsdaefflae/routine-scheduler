"""A `docs/designs.md` ORDER that no item row records — decided work the backlog cannot see.

Every `## ` entry in docs/designs.md is an operator order the builder reads (CLAUDE.md), and
self-audit gives each one a decision row so the Messages page and its counts can see it (its
write-report rule 6b). Nothing checked that it did. Measured 2026-11-05: 36 entries, 2 with a live
row — so the live count read 42 while 30-odd decided pieces of work stood outside it, and five of
the entries were open QUESTIONS that no surface the operator reads ever showed (those are now
refused at the source: tests/test_policy.py::test_designs_md_holds_orders_never_open_questions).

This is the fourth orphan kind on the Messages banner (`kind: "unrecorded-order"`): an entry whose
heading no finding or decision row quotes. A row records an entry by quoting its heading — rule
6b's `Decided via docs/designs.md: "<heading>"` — matched case-insensitively, with the heading's
trailing `(decided …)` / `(<review ids>)` parenthetical optional, since a row names the work, not
the date. Like the other three kinds it SURFACES and never gates. An instance with no ledger at
all (no finding or decision anywhere) has no one to record anything, so it reports nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

#: A trailing parenthetical on a heading — "(decided 2026-10-08, in conversation)", "(E1.6)".
_TAIL = re.compile(r"\s*\([^()]*\)\s*$")


def designs_path(source_repo: Path) -> Path:
    return Path(source_repo) / "docs" / "designs.md"


def headings(source_repo: Path) -> list[str]:
    """Every `## ` entry of docs/designs.md, in file order; [] when the file is absent."""
    try:
        text = designs_path(source_repo).read_text(encoding="utf-8")
    except OSError:
        return []
    return [h.strip() for h in re.findall(r"^## (.+)$", text, flags=re.MULTILINE)]


def _core(heading: str) -> str:
    return _TAIL.sub("", heading).strip().lower()


def unrecorded(source_repo: Path, items: list[dict]) -> list[dict]:
    """`{kind, heading}` for each entry no finding or decision row quotes."""
    ledger = [i for i in items if i.get("type") in ("finding", "decision")]
    if not ledger:
        return []
    corpus = "\n".join(f"{i.get('title') or ''}\n{i.get('detail') or ''}" for i in ledger).lower()
    return [{"kind": "unrecorded-order", "heading": h}
            for h in headings(source_repo) if _core(h) and _core(h) not in corpus]
