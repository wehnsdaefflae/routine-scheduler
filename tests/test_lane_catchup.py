"""Boot catch-up for scheduled lanes (daemon/lane_catchup.py + rsched/lane_fires.py).

The lane fire table is process memory, so a fire that came due while the daemon was down
was simply lost — and with every member cron suppressed by its lane (D71), nothing else
fired those routines. The watermark of each lane's last ARM is on disk now, and boot makes
up the most recent due fire once when the watermark is older than it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import FakeRunner
from rsched import lane_fires, lane_runs, lanes
from rsched.config import ServerConfig
from rsched.daemon import lane_catchup
from rsched.daemon.events import EventBus
from rsched.daemon.scheduler import Scheduler


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    return s


def _events(server) -> list[dict]:
    p = server.routines_home / ".control" / "health-events.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_arm_stamps_the_watermark(tmp_path):
    home = tmp_path
    lane = lanes.create(home, name="N", cron="0 7 * * *", tz="UTC")
    assert lane_fires.last_armed(home, lane["id"]) is None
    rec = lane_runs.arm(home, lane, default_on_failure="continue", armed_by="schedule")
    assert rec is not None
    assert lane_fires.last_armed(home, lane["id"]) == datetime.fromisoformat(rec["created"])


def test_first_boot_stamps_and_arms_nothing(tmp_path):
    """No watermark = no evidence of a miss: the first boot after the upgrade must not fire
    every lane at once. It leaves a mark, and every later boot has something to compare."""
    server = _server(tmp_path)
    lane = lanes.create(server.routines_home, name="N", cron="0 7 * * *", tz="UTC")
    now = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
    assert lane_catchup.boot_catchup(server, now) == []
    assert lane_fires.last_armed(server.routines_home, lane["id"]) == now
    assert lane_runs.read(server.routines_home, lane["id"]) is None


def test_missed_fire_is_made_up_once(tmp_path):
    """The watermark says the last arm was Monday; the cron came due Tuesday 08:30 and the
    daemon was down: boot arms ONE chain (armed_by=catchup) and records the event. A lane
    armed since its last due fire is left alone, however many fires it missed before that."""
    server = _server(tmp_path)
    home = server.routines_home
    lane = lanes.create(home, name="Biweekly", cron="30 8 * * 2,4", tz="UTC")
    fresh = lanes.create(home, name="Fresh", cron="0 7 * * *", tz="UTC")
    lane_fires.stamp(home, lane["id"], datetime(2026, 9, 7, 8, 30, tzinfo=UTC).isoformat())
    lane_fires.stamp(home, fresh["id"], datetime(2026, 9, 10, 7, 0, 5, tzinfo=UTC).isoformat())
    now = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)          # Thursday; Tue 08:30 was missed
    assert lane_catchup.boot_catchup(server, now) == [lane["id"]]
    rec = lane_runs.read(home, lane["id"])
    assert rec is not None and rec["armed_by"] == "catchup"
    assert lane_runs.read(home, fresh["id"]) is None
    evs = [e for e in _events(server) if e["event"] == "lane_fire_catchup"]
    assert len(evs) == 1 and evs[0]["routine"] == lane["id"] and "Biweekly" in evs[0]["detail"]
    # the arm moved the watermark past the due fire: a second boot makes up nothing more
    assert lane_catchup.boot_catchup(server, now + timedelta(minutes=5)) == []


def test_skip_policy_paused_and_in_flight_lanes_are_not_made_up(tmp_path):
    server = _server(tmp_path)
    home = server.routines_home
    old = datetime(2026, 9, 1, 7, 0, tzinfo=UTC).isoformat()
    skipper = lanes.create(home, name="S", cron="0 7 * * *", tz="UTC")
    lanes.update(home, skipper["id"], catchup="skip")
    paused = lanes.create(home, name="P", cron="0 7 * * *", tz="UTC")
    lanes.update(home, paused["id"], paused=True)
    busy = lanes.create(home, name="B", cron="0 7 * * *", tz="UTC")
    unscheduled = lanes.create(home, name="U")
    for rec in (skipper, paused, busy, unscheduled):
        lane_fires.stamp(home, rec["id"], old)
    lane_runs.arm(home, busy, default_on_failure="continue")     # a chain already in flight
    lane_fires.stamp(home, busy["id"], old)                        # …that predates the due fire
    now = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
    assert lane_catchup.boot_catchup(server, now) == []
    assert lane_runs.read(home, skipper["id"]) is None
    assert lane_runs.read(home, paused["id"]) is None
    assert lane_runs.read(home, busy["id"])["armed_by"] == "ui"   # untouched
    assert not [e for e in _events(server) if e["event"] == "lane_fire_catchup"]


async def test_scheduler_boot_catchup_arms_missed_lanes(make_routine, tmp_path):
    """The scheduler's boot_catchup runs the lane half beside the routine half."""
    make_routine(slug="member")
    server = _server(tmp_path)
    home = server.routines_home
    lane = lanes.create(home, name="Nightly", members=[{"slug": "member"}],
                        cron="0 2 * * *", tz="UTC")
    lane_fires.stamp(home, lane["id"], (datetime.now(UTC) - timedelta(days=3)).isoformat())
    sched = Scheduler(server, FakeRunner(), EventBus())
    sched.rescan()
    from rsched.daemon import pause

    pause.set_paused(server, True)
    await sched.boot_catchup()
    assert lane_runs.read(home, lane["id"]) is None
    pause.set_paused(server, False)
    await sched.boot_catchup()
    rec = lane_runs.read(home, lane["id"])
    assert rec is not None and rec["armed_by"] == "catchup"


async def test_a_pause_skipped_lane_fire_is_not_made_up_at_the_next_boot(make_routine, tmp_path,
                                                                        monkeypatch):
    """A deliberate skip is a HANDLED fire, so it moves the watermark.

    Only an arm stamped it before, so a fire the operator's global pause skipped still read as
    unarmed — and the daemon restarts nightly, so the next boot fired the whole chain and told
    the health stream the daemon had been down. `pause` promises the opposite in as many words:
    resuming does not backlog-fire what came due while paused.
    """
    import asyncio

    import rsched.daemon.scheduler as sched_mod
    from conftest import FakeRunner
    from rsched.daemon import pause
    from rsched.daemon.events import EventBus
    from rsched.daemon.scheduler import Scheduler

    make_routine(slug="member")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    server = _server(tmp_path)
    home = server.routines_home
    lane = lanes.create(home, name="Daily", members=[{"slug": "member"}],
                        cron="0 7 * * *", tz="UTC")
    stale = datetime.now(UTC) - timedelta(days=3)
    lane_fires.stamp(home, lane["id"], stale.isoformat())
    pause.set_paused(server, True)
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.lane_next_fires[lane["id"]] = datetime.now(UTC) - timedelta(seconds=1)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if (lane_fires.last_armed(home, lane["id"]) or stale) > stale:
            break
    task.cancel()

    assert fr.fired == []                                    # paused: the chain never armed
    assert lane_runs.read(home, lane["id"]) is None
    pause.set_paused(server, False)
    assert lane_catchup.boot_catchup(server, datetime.now(UTC)) == []
    assert not [e for e in _events(server) if e["event"] == "lane_fire_catchup"]


def test_concurrent_stamps_keep_both_watermarks(tmp_path, monkeypatch):
    """One file holds every lane's watermark and is rewritten whole, and three contexts write
    it: the scheduler on the loop thread, the web layer's sync handlers on FastAPI's
    threadpool, and a root conversation's engine PROCESS through `manage_lane`. Unlocked, two
    overlapping stamps each read, each write, and one is lost — which is exactly the
    precondition for a spurious make-up chain at the next boot.

    The read-modify-write window is widened deterministically, so this fails on the unlocked
    version every time rather than when the scheduler happens to lose the coin toss.
    """
    import threading
    import time
    home = tmp_path
    made = [lanes.create(home, name=f"L{i}", cron="0 7 * * *", tz="UTC") for i in range(6)]
    real_load = lane_fires.load
    monkeypatch.setattr(lane_fires, "load",
                        lambda h: (real_load(h), time.sleep(0.02))[0])
    start = threading.Barrier(len(made))

    def stamp(lane):
        start.wait()
        lane_fires.stamp(home, lane["id"])

    threads = [threading.Thread(target=stamp, args=(rec,)) for rec in made]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(real_load(home)) == sorted(rec["id"] for rec in made)


def test_a_lane_is_armed_once_even_under_a_concurrent_arm(tmp_path, monkeypatch):
    """'One in-flight chain per lane' is an exists-then-write, and its three writers sit in
    three different contexts — so the check and the write have to be one critical section or a
    lane fires two chains at once."""
    import threading
    import time
    home = tmp_path
    lane = lanes.create(home, name="Nightly", cron="0 7 * * *", tz="UTC")
    real_new_id = lane_runs.new_id
    monkeypatch.setattr(lane_runs, "new_id",
                        lambda: (time.sleep(0.02), real_new_id())[1])
    start = threading.Barrier(5)
    armed: list[dict] = []
    guard = threading.Lock()

    def arm():
        start.wait()
        rec = lane_runs.arm(home, lane, default_on_failure="continue", armed_by="ui")
        if rec is not None:
            with guard:
                armed.append(rec)

    threads = [threading.Thread(target=arm) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(armed) == 1
