"""What a DECLINED lane make-up leaves on the record (daemon/lane_catchup.py's resume).

A pause-skipped lane fire has three fates and only two were on the record: the skip
(`lane_fire_paused`) and the make-up (`lane_fire_catchup`). The third — the resume looking at
an owed fire and declining it, because it came due too long ago for the lane's own cadence —
was a `log.info`, and the journal is the one surface this instance's own audit cannot read. So
a reader saw the fire dropped and then nothing, and a declined fire was indistinguishable from
a daemon that never reached the resume at all.

Measured 2026-10-15, which is why these tests exist: the Mon/Wed/Fri maintenance lane's
Wednesday 03:00 fire was skipped by the 10-14 pause, was owed 13.9 h against its own 48 h
cadence — past the quarter-cadence ratio — and was declined. Its five members
(`routine-improver`, `rules-review`, `config-optimizer`, `global-utils-review`,
`steward-hub-maintainer`) stood dark Monday to Friday, and the only record was a log line.

Its own file because `tests/test_lane_catchup.py` is over the size bar and may only shrink.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers import health_events, tmp_server
from rsched import firetimes, lane_fires, lane_runs, lanes
from rsched.daemon import lane_catchup
from rsched.readmodels.health_stream import BLOCKED_EVENTS


def _owing_lane(home, name: str, cron: str, anchor: datetime):
    """A scheduled lane whose last due fire before `anchor` was dropped by a pause."""
    lane = lanes.create(home, name=name, cron=cron, tz="UTC")
    due = firetimes.last_due_fire(lanes.schedulable(lane), anchor)
    lane_fires.stamp(home, lane["id"], due.isoformat())
    lane_fires.stamp_paused_skip(home, lane["id"], due.isoformat())
    return lane, due


def test_a_declined_make_up_says_so_on_a_surface_a_reader_can_reach(tmp_path):
    """The decline is due work that did not happen, so it belongs in BLOCKED_EVENTS."""
    server = tmp_server(tmp_path)
    home = server.routines_home
    lane, due = _owing_lane(home, "Triweekly Maintenance", "0 3 * * 1,3,5",
                            datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
    # 13.9 h owed against this lane's own 48 h cadence — past the quarter, so declined
    now = due + timedelta(hours=13, minutes=52)

    assert lane_catchup.resume_catchup(server, now) == []
    assert lane_runs.read(home, lane["id"]) is None
    assert not health_events(home, event="lane_fire_catchup")

    evs = health_events(home, event="lane_catchup_declined")
    assert len(evs) == 1
    ev = evs[0]
    assert ev["routine"] == lane["id"] and ev["run_id"] == ""
    assert "Triweekly Maintenance" in ev["detail"]
    assert "NOT made up" in ev["detail"]
    assert ev["cron"] == "0 3 * * 1,3,5"
    # the cost of a dropped fire is the cadence, so the reader is told when it fires next
    assert ev["next_fire"] == firetimes.next_fire(lanes.schedulable(lane), now).isoformat()
    # readable by the blocked-work fold, not merely present in the file (F678's class)
    assert "lane_catchup_declined" in BLOCKED_EVENTS
    assert "not made up" in BLOCKED_EVENTS["lane_catchup_declined"].lower()


def test_a_made_up_lane_files_no_decline(tmp_path):
    """The two outcomes are exclusive: exactly one is on the record per decided fire."""
    server = tmp_server(tmp_path)
    home = server.routines_home
    lane, due = _owing_lane(home, "Weekly Research", "0 5 * * 6",
                            datetime(2026, 9, 26, 12, 0, tzinfo=UTC))

    assert lane_catchup.resume_catchup(server, due + timedelta(hours=10)) == [lane["id"]]
    assert len(health_events(home, event="lane_fire_catchup")) == 1
    assert not health_events(home, event="lane_catchup_declined")


def test_the_owed_record_is_spent_either_way(tmp_path):
    """A decline still CONSUMES the owed stamp, so the next resume makes up nothing — and the
    event is filed once, not once per resume."""
    server = tmp_server(tmp_path)
    home = server.routines_home
    lane, due = _owing_lane(home, "Triweekly Maintenance", "0 3 * * 1,3,5",
                            datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
    now = due + timedelta(hours=13, minutes=52)

    assert lane_catchup.resume_catchup(server, now) == []
    assert lane_fires.last_paused_skip(home, lane["id"]) is None
    assert lane_catchup.resume_catchup(server, now + timedelta(minutes=5)) == []
    assert len(health_events(home, event="lane_catchup_declined")) == 1
