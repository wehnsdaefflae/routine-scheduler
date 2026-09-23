"""Terminal report notices are delivered without creating another reply obligation."""

import pytest

from rsched.engine.admin_handlers import handle_report
from rsched.engine.inbox import drain_messages
from rsched.reports import stamp_delivered
from test_reports import _loop, _routine, _rows


@pytest.mark.parametrize("disposal", ["answers", "settles"])
def test_terminal_notice_survives_drain_without_becoming_owed(tmp_path, disposal):
    sender, home = _loop(tmp_path, slug="sender")
    recipient = _routine(home, "recipient")
    handle_report(sender, {"target": "recipient", "title": "ordinary work"})
    ordinary = drain_messages(recipient, tmp_path / "ordinary-consumed")
    assert stamp_delivered(home, ordinary, run_id="recipient:20260921-010000") == ["R1"]
    back, _ = _loop(tmp_path, slug="recipient")
    back.ctx.reports_open = ["R1"]
    fields = {"answers": "R1"} if disposal == "answers" else {"settles": ["R1"]}
    handle_report(back, {"target": "sender", "title": "completed", "closes": True, **fields})
    notices = drain_messages(home / "sender", tmp_path / "notice-consumed")
    assert len(notices) == 1
    assert notices[0].get("closes") is True
    assert stamp_delivered(home, notices, run_id="sender:20260921-020000") == []
    assert next(row for row in _rows(home) if row["id"] == "R2")["delivered"]
