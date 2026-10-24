"""The util-stats snapshot is computed ONCE in the daemon, not by every engine at run end
(D-read-models (a)).

Before this, `engine/runtime.py` called `write_util_stats_snapshot(server)` at the end of
EVERY root run: each engine process re-walked the library's git log, re-parsed the whole
workflow-usage stream and re-scanned every un-counted transcript, to write a file no engine
ever reads (only the Stats tab and the `util-stats` util read it). One daemon owns that now,
debounced on run events, so the per-run cost is gone and a burst of finishes is one pass.

What is pinned here: the engine path is GONE (not merely bypassed), the burst collapses to one
compute, a non-run event wakes nothing, the compute happens off the event loop (it is seconds
of git + file I/O), a raise cannot kill the long-lived listener, and the watch is actually
mounted in the daemon's lifespan — a listener nothing starts is prose (`test_marker_watch`'s
lesson).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path

from rsched.daemon import util_stats_watch
from rsched.daemon.events import EventBus


def _patch_windows(monkeypatch, quiet: float = 0.02, cap: float = 0.3) -> None:
    monkeypatch.setattr(util_stats_watch, "_QUIET_S", quiet, raising=True)
    monkeypatch.setattr(util_stats_watch, "_MAX_COALESCE_S", cap, raising=True)


def _count_computes(monkeypatch, calls: list, *, boom: bool = False) -> None:
    """Stand in for the real snapshot write — the point here is WHEN it is called."""
    from rsched.readmodels import util_stats as util_stats_mod

    def fake(server) -> dict:
        calls.append(threading.get_ident())
        if boom:
            raise OSError("the state dir went away")
        return {"utils": []}

    monkeypatch.setattr(util_stats_mod, "write_util_stats_snapshot", fake)


async def _listen(bus: EventBus, server=None) -> asyncio.Task:
    task = asyncio.create_task(util_stats_watch.bus_listener(server or object(), bus))
    await asyncio.sleep(0)          # let it reach `bus.subscribe()`
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_the_engine_no_longer_computes_the_snapshot_at_run_end():
    """The move must REMOVE the per-run cost, not add a second writer beside it: every engine
    paying for a read model no engine reads is the whole finding.
    """
    from rsched.engine import runtime

    source = Path(runtime.__file__).read_text(encoding="utf-8")
    assert "write_util_stats_snapshot" not in source, (
        "engine/runtime.py still computes the util-stats snapshot at run end")


async def test_a_burst_of_run_events_is_one_recompute(monkeypatch):
    """Twenty runs finishing in a window must cost ONE walk of the library git log, the usage
    stream and the transcripts — not twenty."""
    _patch_windows(monkeypatch)
    calls: list = []
    _count_computes(monkeypatch, calls)
    bus = EventBus()
    task = await _listen(bus)
    try:
        for i in range(20):
            bus.publish({"event": "run_finished", "routine": "r", "run_id": f"r:{i}"})
        await asyncio.sleep(0.25)
        assert calls == calls[:1] and len(calls) == 1, calls
    finally:
        await _stop(task)


async def test_an_event_that_is_not_a_run_event_wakes_nothing(monkeypatch):
    """Every LLM call of every live run is a bus event (`llm_task`/`llm_process`). Recomputing
    on those would be far WORSE than the per-run write this replaces."""
    _patch_windows(monkeypatch)
    calls: list = []
    _count_computes(monkeypatch, calls)
    bus = EventBus()
    task = await _listen(bus)
    try:
        for event in ("llm_task", "llm_process", "question_asked", "question_answered"):
            bus.publish({"event": event})
        await asyncio.sleep(0.25)
        assert calls == []
    finally:
        await _stop(task)


async def test_the_compute_runs_off_the_event_loop(monkeypatch):
    """It is a git subprocess plus every un-counted transcript — seconds, not milliseconds. On
    the loop it would hold every fire, every stream and every request behind it."""
    _patch_windows(monkeypatch)
    calls: list = []
    _count_computes(monkeypatch, calls)
    bus = EventBus()
    task = await _listen(bus)
    try:
        bus.publish({"event": "run_finished"})
        await asyncio.sleep(0.25)
        assert len(calls) == 1
        assert calls[0] != threading.get_ident(), "the snapshot was computed ON the loop"
    finally:
        await _stop(task)


async def test_a_raising_compute_never_kills_the_listener(monkeypatch):
    """The daemon is long-lived: a telemetry write that raises must not end the subscriber and
    leave the snapshot frozen for the process's life, silently."""
    _patch_windows(monkeypatch)
    calls: list = []
    _count_computes(monkeypatch, calls, boom=True)
    bus = EventBus()
    task = await _listen(bus)
    try:
        bus.publish({"event": "run_finished"})
        await asyncio.sleep(0.25)
        assert len(calls) == 1 and not task.done()
        bus.publish({"event": "run_finished"})     # …and it is still listening
        await asyncio.sleep(0.25)
        assert len(calls) == 2 and not task.done()
    finally:
        await _stop(task)


def test_the_watch_is_mounted_in_the_daemons_lifespan():
    """A listener nothing starts is prose. It is started where every other long-lived daemon
    task is (`web/appwiring.py`'s lifespan), observed like them so it cannot die silently, and
    stopped on the way out.
    """
    from rsched.web import appwiring

    source = Path(appwiring.__file__).read_text(encoding="utf-8")
    assert "util_stats_watch.bus_listener(server, bus)" in source
    assert '_observe(utilstats_task, "util-stats snapshot watch")' in source
    assert "utilstats_task" in source.split("yield")[1], (
        "the watch is never stopped at shutdown")
