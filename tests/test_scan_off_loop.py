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


def _fast(monkeypatch) -> None:
    monkeypatch.setattr(readmodel_warm, "_QUIET_S", 0.02)
    monkeypatch.setattr(readmodel_warm, "_MAX_COALESCE_S", 0.3)


def _count_warms(monkeypatch, *, boom: bool = False) -> list[bool]:
    """Stand in for both computes; records whether each pass ran on the event loop."""
    from rsched.readmodels import change_effects
    from rsched.web import decisions_read

    passes: list[bool] = []

    def effects(_home):
        passes.append(threading.current_thread() is threading.main_thread())
        if boom:
            raise OSError("the usage stream went away")
        return {"routines": {}}

    monkeypatch.setattr(change_effects, "changes", effects)
    monkeypatch.setattr(decisions_read, "open_decisions", lambda _server: [])
    return passes


async def _listen(bus: EventBus) -> asyncio.Task:
    from types import SimpleNamespace

    task = asyncio.create_task(readmodel_warm.bus_listener(
        SimpleNamespace(routines_home=Path("/nonexistent")), bus))
    for _ in range(50):                 # the boot pass runs before the first event is read
        await asyncio.sleep(0.01)
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_one_pass_at_boot_then_one_per_burst_off_the_loop(monkeypatch):
    _fast(monkeypatch)
    passes = _count_warms(monkeypatch)
    bus = EventBus()
    task = await _listen(bus)
    try:
        assert passes == [False]                                  # the boot pass
        for i in range(20):
            bus.publish({"event": "run_finished", "routine": "r", "run_id": f"r:{i}"})
        await asyncio.sleep(0.4)
        assert passes == [False, False]                           # twenty events, one pass
        bus.publish({"event": "question_answered"})
        await asyncio.sleep(0.4)
        assert passes == [False, False, False]
    finally:
        await _stop(task)


async def test_model_calls_wake_nothing_and_a_raise_kills_nothing(monkeypatch):
    _fast(monkeypatch)
    passes = _count_warms(monkeypatch, boom=True)
    bus = EventBus()
    task = await _listen(bus)
    try:
        for event in ("llm_task", "llm_process"):
            bus.publish({"event": event})
        await asyncio.sleep(0.3)
        assert len(passes) == 1                                   # only the boot pass
        bus.publish({"event": "run_finished"})
        await asyncio.sleep(0.4)
        assert len(passes) == 2 and not task.done()               # raised, still listening
    finally:
        await _stop(task)


def test_an_app_without_the_scheduler_starts_no_warmer(tmp_path, monkeypatch):
    started: list[object] = []

    async def record(server, bus):
        started.append(server)

    monkeypatch.setattr(readmodel_warm, "bus_listener", record)
    with authed_client(make_test_server(tmp_path)) as c:
        assert c.get("/api/status").status_code == 200
    assert started == []
