"""An operator answer to a report-born question reaches the REPORTS LEDGER — routed or not."""

from __future__ import annotations

import json
from pathlib import Path

from rsched.hooks import reportproj
from rsched.reports import reports_path
from rsched.web import report_answers


def _rows(home: Path) -> list[dict]:
    path = reports_path(home)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_an_unrouted_report_records_the_answer(tmp_path):
    """The case with no other reader: nobody is told, so the ledger must be."""
    assert report_answers.record(tmp_path, "R3630", "Both", routine="")
    row, = [r for r in _rows(tmp_path) if r.get("event") == "answered"]
    assert row["id"] == "R3630"
    assert row["by"] == "Both"


def test_the_answered_event_is_not_terminal():
    """`settled` is for the two events that END a thread. An answer decides the work; it does
    not dispose of the row, and a triage pass must still see it."""
    assert "answered" not in reportproj.TERMINAL_EVENTS
    hook = reportproj.event_hook(report_id="R1", event="answered", by="Both")
    assert hook.disposition == "open"


def test_a_routed_report_is_recorded_and_its_owner_is_still_told(tmp_path, monkeypatch):
    """The F621 relay keeps working, and the ledger row names the routine that must act."""
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "routine.yaml").write_text("slug: owner\n")
    told: list[tuple] = []
    monkeypatch.setattr("rsched.engine.inbox.file_message",
                        lambda d, text, **kw: told.append((d, text, kw)))

    class Server:
        routines_home = tmp_path

    report_answers.relay(Server(), {"report": "R9", "report_target": "owner",
                                    "routine": "asker"}, "do (b)")
    row, = [r for r in _rows(tmp_path) if r.get("event") == "answered"]
    assert (row["id"], row["by"], row["to"]) == ("R9", "do (b)", "owner")
    assert told and told[0][0] == tmp_path / "owner"
    assert "[decision on R9]" in told[0][1]
    assert told[0][2]["name"] == "decision-R9"      # one message per report, not per answer


def test_nothing_is_written_without_a_report_or_an_answer(tmp_path):
    assert not report_answers.record(tmp_path, "", "Both")
    assert not report_answers.record(tmp_path, "R1", "   ")
    assert not reports_path(tmp_path).exists()


def test_a_long_answer_is_cut_to_a_ledger_sized_handle(tmp_path):
    """A ledger row is a handle, not a transcript — the whole answer lives in the question's
    own record. Newlines collapse so one answer stays one row."""
    report_answers.record(tmp_path, "R2", "x" * 900)
    report_answers.record(tmp_path, "R3", "first line\n\nsecond line")
    rows = {r["id"]: r for r in _rows(tmp_path) if r.get("event") == "answered"}
    assert len(rows["R2"]["by"]) == report_answers.ANSWER_MAX
    assert rows["R3"]["by"] == "first line second line"
