"""The reviewer-feedback channel's durable state (web/api_audit): the answered-decision markers
that keep an answered audit decision from re-presenting once a run drains its message."""

from __future__ import annotations

from conftest import hammer
from rsched.paths import read_json
from rsched.web.api_audit import Feedback, write_feedback


def test_concurrent_decision_answers_keep_every_marker(tmp_path):
    """Answers arrive on worker threads — the Messages page's feedback route and the Decisions
    page's answer route both write here — and two read-modify-writes of the marker file at
    once kept one of them: the other decision re-presented as open, and the routine got the
    same answer injected again. Hammered like this, 31 of 180 markers survived unlocked."""
    (tmp_path / "inbox").mkdir()

    def answer(tag: int) -> None:
        for i in range(30):
            write_feedback(tmp_path, Feedback(kind="decision", target=f"D{tag}{i:03d}",
                                              choice="yes"))

    assert hammer(answer) == []
    markers = read_json(tmp_path / "audit" / "decisions-answered.json")
    assert isinstance(markers, dict) and len(markers) == 6 * 30
    assert len(list((tmp_path / "inbox").glob("msg-*.json"))) == 6 * 30


def test_the_same_answer_filed_twice_queues_one_message(tmp_path):
    """F772: answering ONE audit decision wrote two byte-identical inbox messages a second
    apart, so the next run read the same answer twice. Nothing in this path was idempotent.

    The cause is a console race (`static/views/questions.js:436` defers a repaint only while
    an INPUT/TEXTAREA/SELECT holds focus, never for a BUTTON, so answering repaints the list
    mid-POST, replaces the locked form with a fresh enabled one, and the card still reads
    unanswered — measured over `.ui-traces/`: 60 doubled click pairs in two days, none under
    400 ms, so not one was a double-fire). This is the floor under it: the stem is derived
    from the feedback's own content, which is the same mechanism a report delivery already
    uses to make a re-delivery replace its queued message.
    """
    (tmp_path / "inbox").mkdir()
    same = Feedback(kind="decision", target="D215", choice="Both: the guidance now")

    first = write_feedback(tmp_path, same)
    second = write_feedback(tmp_path, same)

    assert first == second, "the same answer filed twice must name the same message"
    queued = list((tmp_path / "inbox").glob("msg-*.json"))
    assert len(queued) == 1, f"a repeated identical answer queued {len(queued)} messages"


def test_a_different_answer_to_the_same_decision_still_files(tmp_path):
    """The guard must not swallow a CHANGED mind: the operator re-answered all six decisions
    on 2026-10-25 and two answers differed (D215 reversed, D212 moved). A content key is what
    keeps that distinction — collapsing per decision would have hidden the second answer.
    """
    (tmp_path / "inbox").mkdir()
    write_feedback(tmp_path, Feedback(kind="decision", target="D215", choice="leave as-is"))
    write_feedback(tmp_path, Feedback(kind="decision", target="D215",
                                      choice="Both: the guidance now"))

    queued = list((tmp_path / "inbox").glob("msg-*.json"))
    assert len(queued) == 2, f"a changed answer was swallowed: {len(queued)} message(s) queued"
