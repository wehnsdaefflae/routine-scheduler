"""A due fire the GLOBAL PAUSE drops leaves a record — for a routine as well as for a lane.

`daemon/pause_skip.py`'s two writers, and the reader registration without which the console
cannot see either. The lane half was already covered in test_scheduler.py (F573); this file
exists because the ROUTINE half was not, and because the two writers now live in one module
precisely so they cannot drift apart again.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import rsched.daemon.scheduler as sched_mod
from conftest import FakeRunner
from helpers import health_events, tmp_server
from rsched.daemon.events import EventBus
from rsched.daemon.scheduler import Scheduler


async def _wait_for(cond, wait_s=5.0):
    for _ in range(int(wait_s / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_a_paused_routine_skip_says_so_in_the_health_stream(make_routine, tmp_path,
                                                                  monkeypatch):
    """For as long as both paths existed they were asymmetric: the LANE path has written an
    event since F573, while a lane-less routine's skipped fire left only a `log.info` line —
    and the journal is the one surface this instance's own audit cannot read.

    Measured cost of that silence: a global pause held from ~03:00 to 21:52 on 2026-10-13 and
    `scheduler-builder` (cron `0 6,14`, in no lane) missed BOTH of its fires with the health
    stream saying nothing at all, while the six LANE fires the same pause ate were each on the
    record. The run that noticed had to prove it from the absence of a run directory.

    `test_scheduler.py::test_paused_tick_skips_fires_but_advances` pins the skip itself; this
    pins the WITNESS, and asserts on the detail rather than the event name alone — a reader has
    to learn which routine lost a fire and that it is not coming back, or the event is a
    notification that something unspecified happened.
    """
    from rsched.daemon import pause

    make_routine(slug="ticker")               # the fixture's own cron: 0 7 * * 1 (weekly)
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    server = tmp_server(tmp_path, create=False, max_concurrent_runs=2)
    pause.set_paused(server, True)
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.next_fires["ticker"] = datetime.now(UTC) - timedelta(seconds=1)

    assert await _wait_for(lambda: health_events(server.routines_home, event="fire_paused"))
    task.cancel()

    assert fr.fired == []                     # paused: the fire was skipped, not deferred
    ev = health_events(server.routines_home, event="fire_paused")[-1]
    # keyed by the ROUTINE slug, with no run to point at
    assert ev["routine"] == "ticker" and ev["run_id"] == ""
    # the detail must name the routine and say the fire is not made up
    assert "ticker" in ev["detail"] and "paused" in ev["detail"]
    # the cron is what tells a reader how long the gap is — an empty one makes the event
    # unreadable exactly where it matters (this fixture's routine is WEEKLY: the skip costs
    # a week, and a reader who cannot see the cadence cannot tell that from ten minutes)
    assert ev["cron"] == "0 7 * * 1" and ev["next_fire"]


def test_the_blocked_fleet_fold_can_see_a_paused_routine_skip():
    """The reader half: a `fire_paused` row means WORK THAT WAS DUE DID NOT HAPPEN, so it
    belongs in the blocked-fleet fold. This fails if the event is added to the writer's
    vocabulary but not to the reader's — the asymmetry that kept the console blind to all six
    events `readmodels/health_stream.py`'s docstring describes."""
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert "fire_paused" in BLOCKED_EVENTS
    assert BLOCKED_EVENTS["fire_paused"].strip()


async def test_a_dropped_lane_fire_is_recorded_as_owed_and_a_routines_is_not(make_routine,
                                                                             tmp_path,
                                                                             monkeypatch):
    """The asymmetry that REMAINS is deliberate, and this pins it so neither half drifts.

    A lane's dropped fire is stamped as owed, which is what lets `lane_catchup.resume_catchup`
    make it up when the pause lifts. A routine's is not: there is no routine analog, so its
    fire is gone and the event says so. If someone later adds a routine make-up, this test is
    what tells them the event's wording has to change with it.
    """
    from rsched import lane_fires, lanes
    from rsched.daemon import pause

    make_routine(slug="member")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    server = tmp_server(tmp_path, create=False, max_concurrent_runs=2)
    lane = lanes.create(server.routines_home, name="Weekly Research",
                        members=[{"slug": "member"}], cron="0 5 * * 6", tz="UTC")
    pause.set_paused(server, True)
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.next_fires["member"] = datetime.now(UTC) - timedelta(seconds=1)
    sched.lane_next_fires[lane["id"]] = datetime.now(UTC) - timedelta(seconds=1)

    assert await _wait_for(
        lambda: health_events(server.routines_home, event="fire_paused")
        and health_events(server.routines_home, event="lane_fire_paused"))
    task.cancel()

    # the LANE's dropped fire is owed — the stamp the resume reads
    assert lane_fires.last_paused_skip(server.routines_home, lane["id"]) is not None
    # the ROUTINE's is not owed anywhere: no store carries it, and its detail says so
    assert lane_fires.last_paused_skip(server.routines_home, "member") is None
    routine_ev = health_events(server.routines_home, event="fire_paused")[-1]
    assert "not made up" in routine_ev["detail"]
