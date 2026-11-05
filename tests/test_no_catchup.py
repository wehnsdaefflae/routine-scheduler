"""No fire is ever made up (D237, operator 2026-11-05): "Remove all catch-up: a fire missed for
any reason (pause, restart, crash) is skipped."

Until then three mechanisms made up a missed fire: boot made up a routine's last due fire under
`schedule.catchup: run_once` and a scheduled lane's under the lane's own `catchup` (against a
watermark, `.control/lane-fires.json`), and lifting the global pause made up a lane's (D156) or
a routine's (D183) dropped fire while it was still early in its own cadence. These tests pin
the reversal from the outside, through the two moments a make-up used to happen — a boot after
downtime and the resume of a pause — so a rebuilt make-up anywhere on either path fails here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import rsched.daemon.scheduler as sched_mod
from conftest import FakeRunner, authed_client, make_test_server
from helpers import health_events
from rsched import lane_runs, lanes, schedule_once
from rsched.daemon import pause
from rsched.daemon.events import EventBus
from rsched.daemon.scheduler import Scheduler


async def _wait_for(cond, wait_s=5.0):
    for _ in range(int(wait_s / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def _fleet(server, make_routine) -> dict:
    """A lane-less routine and a scheduled lane with one member — both weekly, so each has a
    due fire in the past that no run covered: exactly what the boot make-up used to fire."""
    make_routine(slug="solo")
    make_routine(slug="member")
    return lanes.create(server.routines_home, name="Weekly", members=[{"slug": "member"}],
                        cron="0 5 * * 6", tz="UTC")


def _nothing_made_up(server, fr: FakeRunner) -> None:
    home = server.routines_home
    assert fr.fired == []
    assert lane_runs.in_flight(home) == []
    assert schedule_once.pending_requests(home, "solo") == []
    assert schedule_once.pending_requests(home, "member") == []
    assert not (home / ".control" / "lane-fires.json").exists()     # no watermark is kept
    assert not (home / ".control" / "routine-fires.json").exists()  # no owed-fire store either


async def test_a_fire_missed_while_the_daemon_was_down_is_not_made_up(make_routine, tmp_path):
    with authed_client(make_test_server(tmp_path)) as c:
        server = c.app.state.server
        lane = _fleet(server, make_routine)
        fr = FakeRunner()
        sched = Scheduler(server, fr, EventBus())
        await sched._boot()          # the boot is the moment under test
        _nothing_made_up(server, fr)
        now = datetime.now(UTC)
        assert sched.next_fires["solo"] > now                 # the NEXT fire, never the missed one
        assert sched.lane_next_fires[lane["id"]] > now


async def test_a_fire_skipped_during_a_pause_is_not_made_up_on_resume(make_routine, tmp_path,
                                                                     monkeypatch):
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    with authed_client(make_test_server(tmp_path)) as c:
        server = c.app.state.server
        lane = _fleet(server, make_routine)
        pause.set_paused(server, True)
        fr = FakeRunner()
        sched = Scheduler(server, fr, EventBus())
        task = asyncio.create_task(sched.run_forever())
        await asyncio.sleep(0.05)
        sched.next_fires["solo"] = datetime.now(UTC) - timedelta(seconds=1)
        sched.lane_next_fires[lane["id"]] = datetime.now(UTC) - timedelta(seconds=1)
        assert await _wait_for(
            lambda: health_events(server.routines_home, event="fire_paused")
            and health_events(server.routines_home, event="lane_fire_paused"))
        # the skip is still RECORDED — what it cost is on the stream, with the cadence
        ev = health_events(server.routines_home, event="fire_paused")[-1]
        assert ev["routine"] == "solo" and "not made up" in ev["detail"] and ev["cron"]

        # the resume reports nothing made up because it makes nothing up
        assert c.delete("/api/settings/pause").json() == {"ok": True, "paused": False}
        await asyncio.sleep(0.1)      # several unpaused ticks: nothing is owed, nothing fires
        task.cancel()
        _nothing_made_up(server, fr)
        assert sched.next_fires["solo"] > datetime.now(UTC)


def test_a_leftover_schedule_catchup_key_is_reported_and_read_by_nothing(make_routine):
    """The field is gone (no backwards compatibility: the D237 migration strips it from every
    live routine.yaml). A file that still carries it loads — an unknown `schedule:` key is a
    PROBLEM line, never a refusal, exactly like every other stray key — and the line says what
    replaced it rather than "check the spelling"."""
    import yaml

    from rsched.config import RoutineConfig, load_routine

    d = make_routine(slug="leftover")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["schedule"]["catchup"] = "run_once"
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg, problems = load_routine(d)
    assert cfg is not None and cfg.cron == "0 7 * * 1"
    assert "catchup" not in RoutineConfig.model_fields
    line = next(p for p in problems if p.startswith("schedule.catchup"))
    assert "D237" in line and "skipped" in line and "(ignored)" in line
