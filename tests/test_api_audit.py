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
