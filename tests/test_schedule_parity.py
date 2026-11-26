"""The one-shot spool against the hook stream, report-only: the spool is what is SERVED, a
disagreement is filed, and nothing in the check can change the answer. Both stores are written
through their real writers — `schedule_once.arm` for the spool, `hooks/scheduleproj.record_armed`
/ `record_event` for the stream — so the comparison is tested against the rows the system
appends rather than against a shape this file invented.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import tmp_server
from rsched import schedule_once
from rsched.health_events import HEALTH_EVENTS_FILE
from rsched.hooks import scheduleparity, scheduleproj
from rsched.paths import read_jsonl
from rsched.readmodels import memo

WINDOW_START = -1.0
WINDOW_END = 7.0


@pytest.fixture
def server(tmp_path):
    s = tmp_server(tmp_path)
    (s.routines_home / ".control").mkdir(parents=True, exist_ok=True)
    memo.reset()
    return s


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now + timedelta(days=WINDOW_START), now + timedelta(days=WINDOW_END)


def _arm_both(server, slug: str, *, hours: float = 2.0) -> str:
    """Arm a one-shot the way the UI does: the spool record first, then its stream row."""
    rec = schedule_once.arm(server.routines_home, slug,
                            fire_at=datetime.now(UTC) + timedelta(hours=hours),
                            reason="re-check the seat", requested_by="test")
    scheduleproj.record_armed(server.routines_home, rec, slug)
    memo.reset()
    return str(rec["id"])


def _arm_spool_only(server, slug: str, *, hours: float = 2.0) -> str:
    """The stream's arming write failed — it is best-effort by design."""
    rec = schedule_once.arm(server.routines_home, slug,
                            fire_at=datetime.now(UTC) + timedelta(hours=hours),
                            reason="the stream write failed", requested_by="test")
    memo.reset()
    return str(rec["id"])


def _arm_stream_only(server, slug: str, *, hook_id: str, hours: float = 2.0) -> str:
    """A pending row the spool does not hold — a terminal row never landed."""
    fire = datetime.now(UTC) + timedelta(hours=hours)
    scheduleproj.record_armed(server.routines_home,
                              {"id": hook_id, "fire_at": fire.isoformat(),
                               "reason": "the fired row never landed", "requested_by": "test",
                               "created": datetime.now(UTC).isoformat()}, slug)
    memo.reset()
    return hook_id


def _rows(server) -> list[dict]:
    path = Path(server.routines_home) / ".control" / HEALTH_EVENTS_FILE
    return [r for r in read_jsonl(path)
            if r.get("event") == scheduleparity.DIVERGED_EVENT]


def _served(server, slugs) -> dict[str, list[str]]:
    start, end = _window()
    return scheduleparity.week_one_shots(server, slugs, start, end)


# ---- the verdict is pure, and both directions are a shortfall -----------------------------

def test_the_comparison_names_each_side_by_slug_and_request_id():
    spool = {"alpha": {"so-1": "x", "so-2": "y"}, "beta": {"so-3": "z"}}
    stream = {"alpha": {"so-2": "y"}, "beta": {"so-3": "z", "so-4": "w"}}

    only_spool, only_stream = scheduleparity.compare(spool, stream)

    assert only_spool == ["alpha/so-1"]
    assert only_stream == ["beta/so-4"]


def test_two_agreeing_readings_are_no_divergence():
    rows = {"alpha": {"so-1": "x"}}

    assert scheduleparity.compare(rows, dict(rows)) == ([], [])


def test_the_same_request_at_a_different_instant_is_not_a_divergence():
    """The identity is the REQUEST, not the instant: a spool record re-read after a clock skew
    is the same armed one-shot, and the switch this check stages is about which rows exist."""
    only_spool, only_stream = scheduleparity.compare({"alpha": {"so-1": "09:00"}},
                                                     {"alpha": {"so-1": "09:01"}})

    assert (only_spool, only_stream) == ([], [])


# ---- what is served is the spool, whatever the stream says --------------------------------

def test_an_armed_one_shot_is_served_from_the_spool(server):
    _arm_both(server, "alpha")

    served = _served(server, ["alpha"])

    assert len(served["alpha"]) == 1
    assert _rows(server) == []


def test_a_request_the_stream_never_recorded_is_still_served_and_is_filed(server):
    """`record_armed` is best-effort, so the spool can be ahead. Serving the spool means the
    dashboard is right while the check says the fold is not yet trustworthy."""
    req_id = _arm_spool_only(server, "alpha")

    served = _served(server, ["alpha"])

    assert len(served["alpha"]) == 1, "the spool's fire must still be served"
    rows = _rows(server)
    assert len(rows) == 1, rows
    assert rows[0]["only_in_spool"] == 1 and rows[0]["only_in_stream"] == 0
    assert f"alpha/{req_id}" in rows[0]["detail"]
    assert rows[0]["routine"] == "" and rows[0]["run_id"] == ""


def test_a_pending_stream_row_the_spool_lost_is_not_served_and_is_filed(server):
    """The other direction: after the switch the strip would draw a fire that cannot happen."""
    _arm_stream_only(server, "alpha", hook_id="so-stale")

    served = _served(server, ["alpha"])

    assert served["alpha"] == [], "nothing may be served that the spool does not hold"
    rows = _rows(server)
    assert len(rows) == 1, rows
    assert rows[0]["only_in_stream"] == 1 and rows[0]["only_in_spool"] == 0
    assert "alpha/so-stale" in rows[0]["detail"]


def test_a_fired_one_shot_is_no_divergence_at_all(server):
    """Both stores forget it the same way: the request file is consumed and the stream gets a
    terminal row. A fold that read the arming row alone would report every past fire here."""
    req_id = _arm_both(server, "alpha")
    schedule_once.cancel(server.routines_home, "alpha", req_id)
    scheduleproj.record_event(server.routines_home, hook_id=req_id, event="fired")
    memo.reset()

    served = _served(server, ["alpha"])

    assert served["alpha"] == []
    assert _rows(server) == []


# ---- the check is a diagnostic: it may neither flood nor break the page --------------------

def test_a_standing_disagreement_is_one_row_not_one_per_read(server):
    """The week strip is polled — the operator opens the dashboard as often as he likes — so a
    condition that simply stands must be repeat-governed (`health_cooldown`)."""
    _arm_spool_only(server, "alpha")

    for _ in range(4):
        _served(server, ["alpha"])

    assert len(_rows(server)) == 1


def test_a_broken_comparison_never_changes_the_answer(server, monkeypatch):
    """A diagnostic that takes the dashboard down with it is worse than no diagnostic."""
    _arm_both(server, "alpha")

    def boom(*_a, **_k):
        raise RuntimeError("the fold is broken")

    monkeypatch.setattr(scheduleparity, "stream_fires", boom)
    served = _served(server, ["alpha"])

    assert len(served["alpha"]) == 1
    assert _rows(server) == []


def test_a_fire_outside_the_window_is_neither_served_nor_compared(server):
    """The window is the strip's own; a one-shot armed months out is not a divergence. The
    fold's own horizon already drops it on the stream side, so what this pins is the SPOOL
    reading: an armed request beyond the strip's far edge is not a fire in this window."""
    _arm_both(server, "alpha", hours=24 * 60)

    served = _served(server, ["alpha"])

    assert served["alpha"] == []
    assert _rows(server) == []


def test_a_long_overdue_pending_row_is_outside_the_window_and_not_compared(server):
    """`next_fires` hands back every PENDING row whose instant is already past, whatever the
    horizon — that is its contract, since an unconsumed one-shot is a thing to see. The strip's
    window starts a day back, so a row older than that is not a disagreement about this week."""
    _arm_stream_only(server, "alpha", hook_id="so-ancient", hours=-72)

    served = _served(server, ["alpha"])

    assert served["alpha"] == []
    assert _rows(server) == [], "a row before the strip's back-fill edge is not in this window"
