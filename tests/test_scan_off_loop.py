"""Cold read work leaves the event loop and the request path (2026-11-21).

Timed cold against the live store, three costs sat where a request waits for them:

- `registry.scan` — 3.3 s for the routines home, 2.1 s for conversations, 2.9 s of it
  `load_routine` resolving fs roots on the NAS share (0.4-1.4 s each). The scheduler ran it ON
  the event loop at boot and every `registry_rescan_s`, and four request routes called
  `load_routine` afresh on every call;
- `change_effects.changes` — 4.4 s, the whole fleet folded for one routine's summary line;
- `decisions_read.open_decisions` — 1.1 s, the catalog every open tab polls.

Each test pins where one of them now runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path

from conftest import FakeRunner, authed_client, make_test_server, mk_run
from rsched import registry
from rsched.daemon.events import EventBus
from rsched.daemon.scheduler import Scheduler
from rsched.web import readmodel_warm, servable

# --------------------------------------------------------------------------- the scheduler


async def test_every_home_is_first_scanned_off_the_loop_at_boot(tmp_path, monkeypatch):
    with authed_client(make_test_server(tmp_path)) as c:
        server = c.app.state.server
        seen: list[tuple[Path | None, bool]] = []
        real = registry.scan

        def recording(srv, home=None):
            seen.append((home, threading.current_thread() is threading.main_thread()))
            return real(srv, home)

        monkeypatch.setattr(registry, "scan", recording)
        await Scheduler(server, FakeRunner(), EventBus())._boot()
    first: dict = {}
    for home, on_loop in seen:
        first.setdefault(home, on_loop)
    assert set(first) >= {None, server.conversations_home, server.background_home}
    assert not any(first.values()), f"a cold scan ran on the event loop: {seen}"


async def test_the_periodic_rescan_runs_off_the_loop(tmp_path, monkeypatch):
    with authed_client(make_test_server(tmp_path)) as c:
        server = c.app.state.server
        sched = Scheduler(server, FakeRunner(), EventBus())
        threads: list[bool] = []
        monkeypatch.setattr(registry, "scan", lambda srv, home=None: threads.append(
            threading.current_thread() is threading.main_thread()) or {})
        loop = asyncio.get_running_loop()
        sched._last_scan = loop.time() - server.registry_rescan_s - 1
        await sched._tick_once(loop)
    assert threads == [False]


# --------------------------------------------------------------------------- config reads


def _count_loads(monkeypatch) -> list[Path]:
    loads: list[Path] = []
    real = registry.load_routine

    def counting(d):
        loads.append(Path(d))
        return real(d)

    monkeypatch.setattr(registry, "load_routine", counting)
    return loads


def test_load_config_rereads_only_when_its_files_change(make_routine, monkeypatch):
    routine_dir = make_routine(slug="cfgmemo")
    loads = _count_loads(monkeypatch)
    first, _ = registry.load_config(routine_dir)
    second, _ = registry.load_config(routine_dir)
    assert len(loads) == 1 and first == second and first is not second   # private copies
    path = routine_dir / "routine.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    registry.load_config(routine_dir)
    assert len(loads) == 2


def test_the_served_roots_read_the_memoized_config(api_client, make_routine, monkeypatch):
    client, _ = api_client
    routine_dir = make_routine(slug="rootsmemo")
    server = client.app.state.server
    servable.granted_roots(server, routine_dir)
    loads = _count_loads(monkeypatch)
    for _ in range(3):
        servable.granted_roots(server, routine_dir)
    assert loads == []


def test_the_run_detail_route_reads_the_memoized_config(api_client, make_routine, monkeypatch):
    client, _ = api_client
    routine_dir = make_routine(slug="detailmemo")
    mk_run(routine_dir, "20261121-130000", "finished")
    url = "/api/runs/detailmemo:20261121-130000"
    assert client.get(url).status_code == 200
    loads = _count_loads(monkeypatch)
    for _ in range(3):
        assert client.get(url).status_code == 200
    assert routine_dir not in loads


# --------------------------------------------------------------------------- the warmer


def _fast(monkeypatch, *, settle: float = 0.3) -> None:
    monkeypatch.setattr(readmodel_warm, "_QUIET_S", 0.02)
    monkeypatch.setattr(readmodel_warm, "_MAX_COALESCE_S", 0.3)
    monkeypatch.setattr(readmodel_warm, "BOOT_SETTLE_S", settle)


def _record_steps(monkeypatch, *, boom: bool = False) -> list[str]:
    """Stand in for both computes; records each step as `<model>@<loop|thread>`, in order."""
    from rsched.readmodels import change_effects
    from rsched.web import decisions_read

    steps: list[str] = []

    def where() -> str:
        return "loop" if threading.current_thread() is threading.main_thread() else "thread"

    def effects(_home):
        steps.append(f"effects@{where()}")
        if boom:
            raise OSError("the usage stream went away")
        return {"routines": {}}

    def decisions(_server):
        steps.append(f"decisions@{where()}")
        return []

    monkeypatch.setattr(change_effects, "changes", effects)
    monkeypatch.setattr(decisions_read, "open_decisions", decisions)
    return steps


async def _listen(bus: EventBus, ready: asyncio.Event | None = None) -> asyncio.Task:
    from types import SimpleNamespace

    task = asyncio.create_task(readmodel_warm.bus_listener(
        SimpleNamespace(routines_home=Path("/nonexistent")), bus, ready))
    await asyncio.sleep(0.05)
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_the_boot_pass_waits_for_the_scheduler_and_warms_decisions_first(monkeypatch):
    """2026-11-21: run at once, the boot pass competed with the scheduler's boot scans and
    every reconnecting tab, and `/api/questions` took 25-29 s. It waits for the boot now, warms
    the catalog every tab is waiting on, and leaves the fleet-wide fold until the burst is over.
    """
    _fast(monkeypatch, settle=0.3)
    steps = _record_steps(monkeypatch)
    ready = asyncio.Event()
    task = await _listen(EventBus(), ready)
    try:
        await asyncio.sleep(0.2)
        assert steps == [], "the boot pass ran before the scheduler's boot finished"
        ready.set()
        await asyncio.sleep(0.15)
        assert steps == ["decisions@thread"]                     # the fold is still settling
        await asyncio.sleep(0.4)
        assert steps == ["decisions@thread", "effects@thread"]
    finally:
        await _stop(task)


async def test_a_boot_that_never_finishes_does_not_hold_the_warmer_forever(monkeypatch):
    _fast(monkeypatch, settle=0.05)
    monkeypatch.setattr(readmodel_warm, "BOOT_WAIT_MAX_S", 0.1)
    steps = _record_steps(monkeypatch)
    task = await _listen(EventBus(), asyncio.Event())            # never set
    try:
        await asyncio.sleep(0.5)
        assert steps == ["decisions@thread", "effects@thread"]
    finally:
        await _stop(task)


async def test_a_burst_is_one_pass_decisions_first(monkeypatch):
    _fast(monkeypatch, settle=0.0)
    steps = _record_steps(monkeypatch)
    bus = EventBus()
    task = await _listen(bus)
    try:
        await asyncio.sleep(0.1)
        assert steps == ["decisions@thread", "effects@thread"]   # the boot pass, both halves
        for i in range(20):
            bus.publish({"event": "run_finished", "routine": "r", "run_id": f"r:{i}"})
        await asyncio.sleep(0.4)
        assert steps[2:] == ["decisions@thread", "effects@thread"]   # twenty events, one pass
        bus.publish({"event": "question_answered"})
        await asyncio.sleep(0.4)
        assert steps[4:] == ["decisions@thread", "effects@thread"]
    finally:
        await _stop(task)


async def test_events_inside_the_settle_window_warm_decisions_only(monkeypatch):
    _fast(monkeypatch, settle=0.6)
    steps = _record_steps(monkeypatch)
    bus = EventBus()
    task = await _listen(bus)
    try:
        bus.publish({"event": "run_finished"})
        await asyncio.sleep(0.25)
        assert steps == ["decisions@thread", "decisions@thread"]
        await asyncio.sleep(0.6)
        assert steps[-1] == "effects@thread"                       # the fold, by the clock
    finally:
        await _stop(task)


async def test_model_calls_wake_nothing_and_a_raise_kills_nothing(monkeypatch):
    _fast(monkeypatch, settle=0.0)
    steps = _record_steps(monkeypatch, boom=True)
    bus = EventBus()
    task = await _listen(bus)
    try:
        await asyncio.sleep(0.1)
        booted = len(steps)
        for event in ("llm_task", "llm_process"):
            bus.publish({"event": event})
        await asyncio.sleep(0.3)
        assert len(steps) == booted
        bus.publish({"event": "run_finished"})
        await asyncio.sleep(0.4)
        assert len(steps) == booted + 2 and not task.done()        # raised, still listening
    finally:
        await _stop(task)


async def test_the_scheduler_says_when_its_boot_is_done(tmp_path):
    with authed_client(make_test_server(tmp_path)) as c:
        sched = Scheduler(c.app.state.server, FakeRunner(), EventBus())
        assert not sched.booted.is_set()
        await sched._boot()
        assert sched.booted.is_set()


def test_an_app_without_the_scheduler_starts_no_warmer(tmp_path, monkeypatch):
    started: list[object] = []

    async def record(server, bus):
        started.append(server)

    monkeypatch.setattr(readmodel_warm, "bus_listener", record)
    with authed_client(make_test_server(tmp_path)) as c:
        assert c.get("/api/status").status_code == 200
    assert started == []
