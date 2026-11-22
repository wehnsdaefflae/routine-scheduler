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


def test_an_identical_repeat_answer_adds_no_second_row(tmp_path):
    """One report's answer is one fact. The answer route serves a record until a RUN consumes
    it, so the same question can be posted twice; a reader counting rows must not see two
    decisions where the operator made one."""
    assert report_answers.record(tmp_path, "R10", "do (b)")
    assert not report_answers.record(tmp_path, "R10", "do (b)")
    assert not report_answers.record(tmp_path, "R10", "  do   (b) ")   # same after normalizing
    rows = [r for r in _rows(tmp_path) if r.get("event") == "answered"]
    assert len(rows) == 1


def test_a_corrected_answer_is_a_new_fact_and_is_recorded(tmp_path):
    """Only the LATEST answer is compared: answering again with different words is a
    correction, and returning to an earlier wording after one in between is also a change."""
    report_answers.record(tmp_path, "R11", "do (a)")
    report_answers.record(tmp_path, "R11", "do (b)")
    report_answers.record(tmp_path, "R11", "do (a)")
    assert [r["by"] for r in _rows(tmp_path) if r.get("event") == "answered"] == [
        "do (a)", "do (b)", "do (a)"]


def test_a_repeat_answer_does_not_tell_the_target_again(tmp_path, monkeypatch):
    """The relay's own idempotency key only holds while the message is QUEUED — once the
    target's run has drained it, a repeat post would deliver the same decision a second time.
    A correction still goes out."""
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "routine.yaml").write_text("slug: owner\n")
    told: list[str] = []
    monkeypatch.setattr("rsched.engine.inbox.file_message",
                        lambda d, text, **kw: told.append(text))

    class Server:
        routines_home = tmp_path

    match = {"report": "R12", "report_target": "owner", "routine": "asker"}
    report_answers.relay(Server(), match, "do (b)")
    report_answers.relay(Server(), match, "do (b)")
    assert len(told) == 1
    report_answers.relay(Server(), match, "do (c) instead")
    assert len(told) == 2


def test_an_unreadable_ledger_still_relays_the_answer(tmp_path, monkeypatch):
    """Telling the target twice is a cost; losing his decision because the ledger could not be
    read is not survivable, so the duplicate check fails OPEN."""
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "routine.yaml").write_text("slug: owner\n")
    told: list[str] = []
    monkeypatch.setattr("rsched.engine.inbox.file_message",
                        lambda d, text, **kw: told.append(text))
    monkeypatch.setattr("rsched.web.report_answers.read_jsonl",
                        lambda p: (_ for _ in ()).throw(OSError("unreadable")))

    class Server:
        routines_home = tmp_path

    report_answers.relay(Server(), {"report": "R13", "report_target": "owner",
                                    "routine": "asker"}, "do (b)")
    assert len(told) == 1
