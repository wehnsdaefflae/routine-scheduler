"""A docs/designs.md ORDER no item row records is surfaced (readmodels/design_orders.py).

Measured 2026-11-05: 36 entries, 2 with a live row — decided work outside every count.
"""

from __future__ import annotations

from pathlib import Path

from rsched.readmodels import design_orders


def _repo(tmp_path: Path, *headings: str) -> Path:
    (tmp_path / "docs").mkdir()
    body = "# Designs not yet built\n\n" + "".join(f"## {h}\n\nspec\n\n---\n\n" for h in headings)
    (tmp_path / "docs" / "designs.md").write_text(body, encoding="utf-8")
    return tmp_path


def _row(rid: str, title: str, detail: str = "") -> dict:
    return {"id": rid, "type": "decision", "title": title, "detail": detail}


def test_an_order_no_row_quotes_is_unrecorded(tmp_path):
    repo = _repo(tmp_path, "A goals card in the side panel (decided 2026-10-08, in conversation)",
                 "Every git write goes through libgit (C1.10)")
    rows = [_row("D300", "x", 'Decided via docs/designs.md: "Every git write goes through libgit"')]
    got = design_orders.unrecorded(repo, rows)
    assert got == [{"kind": "unrecorded-order",
                    "heading": "A goals card in the side panel (decided 2026-10-08, in conversation)"}]


def test_a_quoted_heading_matches_with_or_without_its_parenthetical(tmp_path):
    repo = _repo(tmp_path, "Undo a run (decided 2026-10-08, in conversation)")
    rows = [_row("D301", "UNDO A RUN (decided 2026-10-08, in conversation)")]
    assert design_orders.unrecorded(repo, rows) == []


def test_no_ledger_means_nobody_to_record_and_nothing_reported(tmp_path):
    repo = _repo(tmp_path, "Something decided")
    assert design_orders.unrecorded(repo, []) == []
    assert design_orders.unrecorded(repo, [{"id": "R1", "type": "report", "title": "t"}]) == []


def test_an_absent_designs_file_reports_nothing(tmp_path):
    assert design_orders.headings(tmp_path) == []
