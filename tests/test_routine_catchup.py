"""A pause-skipped ROUTINE fire is made up, the way a lane's always was (D183).

`daemon/scheduler.run_forever` advanced the fire table, recorded the skip and moved on, while
`lane_catchup` armed a make-up chain for a lane both at the resume and at boot. Measured
2026-10-14: a pause ate FOUR consecutive fires of `scheduler-builder` (cron `0 6,14`, in no
lane) — it held five handed-over items and shipped nothing for three days, and the only visible
symptom was the absence of a run directory.

D34's "skip, don't backlog" holds for a frequent cron and not for a twice-daily routine, which
loses half a day per skip. So the make-up is decided by the same ratio `lane_catchup` uses: the
fire is made up while less than a QUARTER of that routine's own cadence has passed.

Two things these tests pin that the hand-over did NOT specify, both chosen by measurement:

- the boot `catchup` policy does not gate this. All 34 routines on the live instance read
  `catchup: skip`, `scheduler-builder` included, so gating on it would have shipped the
  decision as a no-op;
- the make-up is ARMED as a one-shot, not fired inline, because the resume is a sync web
  handler off the daemon's loop — the same split `lane_runs.arm` uses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml

from helpers import health_events, tmp_server
from rsched import firetimes, routine_fires, schedule_once
from rsched.daemon import routine_catchup
from rsched.health_events import log_health_event
from rsched.readmodels.health_stream import BLOCKED_EVENTS

#: 06:00 and 14:00 — scheduler-builder's own cron, the routine the decision is about. The
#: gap between its two fires is 8 h, so a quarter is two hours.
BUILDER_CRON = "0 6,14 * * *"


def _routine(home, slug="builder", *, cron=BUILDER_CRON, enabled=True, catchup="skip"):
    d = home / slug
    (d / "state").mkdir(parents=True, exist_ok=True)
    cfg = {"name": f"Test {slug}", "slug": slug, "enabled": enabled,
           "description": "A test routine.",
           "schedule": {"cron": cron, "tz": "UTC", "catchup": catchup},
           "workflow": {"library_slug": "test-flow", "library_commit": "abc123"}}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "main.md").write_text("# step 1\nDo the thing.\n", encoding="utf-8")
    return d


@pytest.fixture
def server(tmp_path):
    srv = tmp_server(tmp_path)
    (srv.routines_home / ".control").mkdir(parents=True, exist_ok=True)
    return srv


def _due(cron: str, before: datetime | None = None) -> datetime:
    """A REAL fire instant of `cron` — never a hand-written one.

    Measured on this host 2026-10-16: every file in /usr/share/zoneinfo is byte-identical to
    Europe/Berlin (`ZoneInfo("UTC").utcoffset()` is +02:00, tzname CEST, and the `tzdata`
    package is absent), so "06:00 UTC" is 08:00 wall time and the gap to the next fire is not
    the cadence. Asking `firetimes` is both correct and independent of the host's zone data.
    """
    cfg = SimpleNamespace(cron=cron, tz="UTC", enabled=True)
    return firetimes.last_due_fire(cfg, before or datetime(2026, 10, 14, 12, 0, tzinfo=UTC))


def _armed(server, slug):
    return [schedule_once.read_request(p)
            for p in schedule_once.pending_requests(server.routines_home, slug)]


# ---- the owed stamp -----------------------------------------------------------------------


def test_the_skip_records_the_fire_as_owed_and_the_event_says_so(server):
    """`fire_paused` alone could not be acted on: it says a fire was dropped, never that it is
    still owed. The stamp is what a catch-up reads."""
    from rsched.daemon import pause_skip

    _routine(server.routines_home)
    due = _due(BUILDER_CRON)
    pause_skip.routine_fire_dropped(server, "builder", BUILDER_CRON, due,
                                    due + timedelta(hours=8))
    assert routine_fires.last_paused_skip(server.routines_home, "builder") == due
    ev = health_events(server.routines_home, event="fire_paused")[-1]
    assert "OWED" in ev["detail"]
    assert ev["cron"] == BUILDER_CRON


def test_a_second_skip_keeps_the_oldest_owed_fire(server):
    """A pause swallowing two of a routine's fires owes it ONE run — and the make-up stands in
    for the work that has waited longest, so the FIRST dropped fire is the one kept."""
    from rsched.daemon import pause_skip

    _routine(server.routines_home)
    first = _due(BUILDER_CRON)
    second = firetimes.next_fire(SimpleNamespace(cron=BUILDER_CRON, tz="UTC", enabled=True),
                                 first)
    for due in (first, second):
        pause_skip.routine_fire_dropped(server, "builder", BUILDER_CRON, due,
                                        due + timedelta(hours=8))
    assert routine_fires.last_paused_skip(server.routines_home, "builder") == first
    assert list(routine_fires.owed(server.routines_home)) == ["builder"]


# ---- the decision -------------------------------------------------------------------------


def test_a_fire_still_early_in_its_cadence_is_made_up(server):
    """The builder's own case: 06:00 skipped, the pause lifted at 07:00. One hour against its
    own 8 h cadence is well under a quarter, so the fire is made up."""
    _routine(server.routines_home)
    due = _due(BUILDER_CRON)
    routine_fires.stamp_paused_skip(server.routines_home, "builder", due.isoformat())

    armed = routine_catchup.catchup(server, due + timedelta(hours=1),
                                    at="when the pause was lifted")
    assert armed == ["builder"]
    requests = _armed(server, "builder")
    assert len(requests) == 1
    assert "make-up fire" in requests[0]["reason"]
    assert requests[0]["requested_by"] == "catchup:when the pause was lifted"
    ev = health_events(server.routines_home, event="fire_catchup")[-1]
    assert "when the pause was lifted" in ev["detail"]
    assert "60 min late" in ev["detail"]
    # decided, so nothing is owed any more — a later resume must not fire it again
    assert routine_fires.owed(server.routines_home) == {}


def test_a_fire_owed_too_long_is_declined_with_both_numbers(server):
    """Past a quarter of the cadence a make-up stops being "the fire, late" and becomes an
    extra fire crowding the next one. The DECLINE is an event, because the absence of a
    catch-up reads exactly like a daemon that never got there."""
    _routine(server.routines_home)
    due = _due(BUILDER_CRON)
    routine_fires.stamp_paused_skip(server.routines_home, "builder", due.isoformat())

    assert routine_catchup.catchup(server, due + timedelta(hours=5), at="at boot") == []
    assert _armed(server, "builder") == []
    ev = health_events(server.routines_home, event="routine_catchup_declined")[-1]
    assert "5.0 h owed" in ev["detail"]
    assert "8.0 h cadence" in ev["detail"]
    assert routine_fires.owed(server.routines_home) == {}, "a decision taken is taken"


@pytest.mark.parametrize(("cron", "late_h", "made_up"), [
    ("0 6,14 * * *", 1, True),      # twice-daily: a quarter of 8 h is 2 h
    ("0 6,14 * * *", 3, False),
    ("0 3 * * *", 5, True),         # daily: a quarter of 24 h is 6 h
    ("0 3 * * *", 7, False),
    ("0 5 * * 1", 40, True),        # weekly: a quarter of 168 h is 42 h
    ("0 5 * * 1", 50, False),
])
def test_the_quarter_cadence_ratio_is_the_same_sentence_for_every_cron(server, cron, late_h,
                                                                      made_up):
    """A RATIO, not a cadence threshold: "twice-daily routines are made up" is a rule the next
    cadence falls through, which is `lane_catchup`'s own measured reason for the ratio."""
    _routine(server.routines_home, cron=cron)
    due = _due(cron)
    routine_fires.stamp_paused_skip(server.routines_home, "builder", due.isoformat())
    armed = routine_catchup.catchup(server, due + timedelta(hours=late_h), at="at boot")
    assert (armed == ["builder"]) is made_up


def test_the_boot_catchup_policy_does_not_gate_a_pause_skip(server):
    """`registry.missed_fire` honours `catchup` because a DOWN daemon may have missed many
    fires. A pause-skipped fire is a different fact: the daemon was up and the operator's own
    pause dropped it. Every routine on the live instance reads `catchup: skip`, so gating on
    that field would have shipped this decision as a no-op."""
    _routine(server.routines_home, catchup="skip")
    due = _due(BUILDER_CRON)
    routine_fires.stamp_paused_skip(server.routines_home, "builder", due.isoformat())
    assert routine_catchup.catchup(server, due + timedelta(minutes=30), at="at boot") == [
        "builder"]


def test_a_switched_off_routine_is_not_made_up_and_stops_owing(server):
    """The operator's state wins over an owed fire — `boot_catchup` reads `fireable` the same
    way — and the stamp must not outlive the decision, or the file accumulates forever."""
    _routine(server.routines_home, enabled=False)
    due = _due(BUILDER_CRON)
    routine_fires.stamp_paused_skip(server.routines_home, "builder", due.isoformat())
    assert routine_catchup.catchup(server, due + timedelta(minutes=30), at="at boot") == []
    assert _armed(server, "builder") == []
    assert routine_fires.owed(server.routines_home) == {}


def test_a_routine_that_no_longer_exists_loses_its_stamp(server):
    due = _due(BUILDER_CRON)
    routine_fires.stamp_paused_skip(server.routines_home, "ghost", due.isoformat())
    assert routine_catchup.catchup(server, due + timedelta(minutes=30), at="at boot") == []
    assert routine_fires.owed(server.routines_home) == {}


def test_nothing_owed_is_cheap_and_silent(server):
    _routine(server.routines_home)
    assert routine_catchup.catchup(server, datetime.now(UTC), at="at boot") == []
    assert health_events(server.routines_home, event="fire_catchup") == []


def test_a_naive_or_unreadable_stamp_reads_as_nothing_owed(server):
    """`lane_fires._instant`'s measured trap: a hand edit of a file documented as safe to
    delete came back naive, and the comparison with an aware instant raised mid-loop."""
    from rsched.paths import atomic_write_json

    atomic_write_json(routine_fires.path(server.routines_home),
                      {"paused-skip:builder": "2026-10-14T06:00:00",      # naive
                       "paused-skip:other": "not a date"})
    assert routine_fires.owed(server.routines_home) == {}
    assert routine_catchup.catchup(server, datetime.now(UTC), at="at boot") == []


# ---- the readers ---------------------------------------------------------------------------


def test_both_new_events_are_in_the_documented_vocabulary():
    """The enum IS the contract: every reader filters on it, so an unlisted event is written
    and read by nobody (`tests/test_health_events.py` walks the call sites in both directions).
    """
    from pathlib import Path

    header = Path(log_health_event.__module__.replace(".", "/"))
    text = (Path(__file__).resolve().parents[1] / f"src/{header}.py").read_text(encoding="utf-8")
    for name in ("fire_catchup", "routine_catchup_declined"):
        assert f'"{name}"' in text, f"{name} is not in health_events.py's header enum"


def test_a_declined_make_up_is_a_blocked_event_the_console_renders():
    """The reader half: a decline means WORK THAT WAS DUE WILL NOT HAPPEN, which is exactly
    what `BLOCKED_EVENTS` is for — `lane_catchup_declined` is registered there for the same
    reason, after its silence left five routines dark from Monday to Friday."""
    assert "routine_catchup_declined" in BLOCKED_EVENTS
    assert BLOCKED_EVENTS["routine_catchup_declined"].strip()
    # …and `fire_paused`'s caption no longer tells the reader a routine skip is never made up
    assert "never made up" not in BLOCKED_EVENTS["fire_paused"]
