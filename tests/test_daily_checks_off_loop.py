"""A watcher's work never runs on the event loop: `_off_tick` starts each watcher as a task ON
the loop, so a `tick` that calls blocking work directly holds every request and fire behind it —
pinned for the owed-work check and the marker scan, and as a rule for every watcher."""

from __future__ import annotations

import inspect
import re
import threading
from pathlib import Path

import rsched.daemon.scheduler as sched_mod
from helpers import tmp_server
from rsched.daemon import marker_watch, owed_watch


async def test_the_owed_work_check_runs_off_the_loop(tmp_path, monkeypatch):
    seen: list[bool] = []
    monkeypatch.setattr(owed_watch.OwedWatch, "check", lambda self, today: seen.append(
        threading.current_thread() is threading.main_thread()))
    watch = owed_watch.OwedWatch(tmp_server(tmp_path))
    await watch.tick()
    await watch.tick()                                  # same day: no second pass
    assert seen == [False]


async def test_the_marker_scan_runs_off_the_loop(tmp_path, monkeypatch):
    seen: list[bool] = []

    def scan(_today):
        seen.append(threading.current_thread() is threading.main_thread())
        return []

    monkeypatch.setattr(marker_watch, "expired", scan)
    monkeypatch.setattr(marker_watch, "due_soon", scan)
    await marker_watch.MarkerWatch(tmp_server(tmp_path)).tick()
    assert seen == [False, False]


async def test_an_owed_check_that_raises_kills_nothing(tmp_path, monkeypatch):
    def boom(self, today):
        raise OSError("a routine dir went away")

    monkeypatch.setattr(owed_watch.OwedWatch, "check", boom)
    await owed_watch.OwedWatch(tmp_server(tmp_path)).tick()   # no raise is the assertion


def _off_tick_watchers() -> dict[str, type]:
    """Every watcher the scheduler starts through `_off_tick`, read from its own source, so a
    watcher added later is covered without an edit here."""
    source = Path(inspect.getsourcefile(sched_mod.Scheduler) or "").read_text(encoding="utf-8")
    started = set(re.findall(r'_off_tick\("[^"]+", self\.(\w+)\.tick\)', source))
    built = dict(re.findall(r"self\.(\w+) = (\w+)\(", source))
    return {attr: getattr(sched_mod, built[attr]) for attr in started if attr in built}


def test_every_off_tick_watcher_hands_its_work_to_a_thread():
    """The structural half: a watcher's class must reach `asyncio.to_thread` somewhere. Not a
    proof that nothing blocks — a guard against the shape all three defects had, where the
    word does not occur in the class at all."""
    watchers = _off_tick_watchers()
    assert len(watchers) >= 7, f"the scan found too few watchers to mean anything: {watchers}"
    missing = [f"{attr} ({cls.__name__})" for attr, cls in sorted(watchers.items())
               if "asyncio.to_thread" not in inspect.getsource(cls)]
    assert not missing, f"these watchers run their work on the event loop: {missing}"
