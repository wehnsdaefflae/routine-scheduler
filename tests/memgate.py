"""A test session WAITS for the host to have the memory it needs before it starts (2026-11-05)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import memhistory
import memwatch

# RE-EXPORTED deliberately: the charges moved to `memcharges` (2026-11-19) and every caller that
# already reads them through this module — `memgate.NEED_MB`, `memgate.fast_need_mb` — keeps
# working. A split that renames its callers' entry points is a split that breaks them.
from memcharges import (  # noqa: F401
    CONTROLLER_HOLD_MB,
    NEED_MB,
    PER_WORKER_HOLD_MB,
    SELECTION_PROSE,
    fast_need_mb,
    need_mb,
    prose_for,
    readings_for,
    selection_for,
)

MEMINFO = Path("/proc/meminfo")
CGROUP = Path("/sys/fs/cgroup")


def available_mb(meminfo: Path = MEMINFO) -> int | None:
    """The host's MemAvailable in MB, or None where it cannot be read."""
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def cgroup_headroom_mb(cgroup: Path = CGROUP) -> int | None:
    """MB this cgroup can still HOLD before its `memory.max`, or None where there is no ceiling
    to read (the host itself, cgroup v1, `max`)."""
    try:
        limit = (cgroup / "memory.max").read_text(encoding="ascii").strip()
        stat = (cgroup / "memory.stat").read_text(encoding="ascii")
    except OSError:
        return None
    anon = next((int(line.split()[1]) for line in stat.splitlines()
                 if line.startswith("anon ")), None)
    if not limit.isdigit() or anon is None:
        return None
    try:
        swap = int((cgroup / "memory.swap.current").read_text(encoding="ascii"))
    except (OSError, ValueError):
        swap = 0
    return (int(limit) - anon - swap) // (1024 * 1024)


def headroom_mb(*, read=None, read_cgroup=None) -> int | None:
    """The MB a selection may actually use: the SMALLER of the host's MemAvailable and this
    cgroup's own headroom — exactly the pair `wait_for_memory` below enforces.
    """
    readers = (read or available_mb, read_cgroup or globals()["cgroup_headroom_mb"])
    have = [mb for mb in (reader() for reader in readers) if mb is not None]
    return min(have) if have else None


def should_wait(config) -> bool:
    """Is this the CONTROLLER of a real gate session — the one process that may block?"""
    return not hasattr(config, "workerinput") and hasattr(config, "pluginmanager")


def wait_for_config(config, markexpr: str) -> None:
    """Wait for what THIS session will really start — the selection and its worker COUNT (D291)."""
    selection = selection_for(config, markexpr)
    workers = getattr(config.option, "numprocesses", 0) or 0
    key = memhistory.selection_key(getattr(config.option, "file_or_dir", []), markexpr, workers)
    # The wait returns what this session may hold — its own priced charge when the memory was
    # there, or what was really free when the deadline ran out first. That figure, never the
    # free headroom in general, is the watchdog's cap: the fast stage legitimately holds
    # 3.08-3.29 GB on a box that often has 2.8 GB available, so capping at whatever happened to
    # be free would kill a healthy release gate (see memwatch.cap_mb).
    memwatch.arm(cap=memwatch.cap_mb(wait_for_memory(selection, workers, key=key),
                                     class_mb=need_mb(selection, workers)),
                 selection=selection, key=key, evidence_dir=memhistory.EVIDENCE_DIR)


def wait_for_memory(selection: str, workers: int = 0, *, key: str = "", read=available_mb,
                    read_cgroup=cgroup_headroom_mb, read_hold=None, sleep=time.sleep,
                    clock=time.monotonic) -> int | None:
    """Block until the host can take `selection` ("fast" | "browser" | "serial"), then say what
    the session may hold: its charge, or — when the deadline ran out first — the MB that were
    actually free, which the watchdog then bounds it at. None when the wait is switched off.
    """
    if os.environ.get("RSCHED_TEST_MEM_WAIT", "") == "0":
        return None
    need, priced_from = (memhistory.priced_for(key, need_mb(selection, workers)) if key
                         else (need_mb(selection, workers), ""))
    what = prose_for(selection, workers) + (f" ({priced_from})" if priced_from else "")
    deadline = clock() + float(os.environ.get("RSCHED_TEST_MEM_WAIT_S") or 1200)
    told = False
    while True:
        short = [(name, have) for name, have in readings_for(selection, read, read_cgroup)
                 if have is not None and have < need]
        if not short:
            return need
        name, have = min(short, key=lambda s: s[1])
        if clock() >= deadline:
            # D295: ADMIT it, bounded by what is actually free, instead of refusing. Refusing
            # guaranteed nothing ran; `memwatch`'s docstring holds the four measurements.
            #
            # The bound is converted into the unit the watchdog READS and the charges above are
            # written in — the whole cgroup's hold — by adding what the cgroup already holds to
            # what is still free. Returning the free MB raw would bound a session BELOW the
            # daemon's own resident hold (measured 1,264 MB on 2026-11-19) and kill it at
            # collection, which is the same unit error that cost this gate its fast stage once.
            bound = int(have) + int((read_hold or memwatch.cgroup_hold_mb)())
            sys.stderr.write(f"memgate: {name} still has only {have} MB and {what} wants "
                             f"{need} MB — STARTING IT ANYWAY, bounded at {bound} MB of cgroup "
                             "hold by the watchdog (tests/memwatch.py), because refusing "
                             "guarantees no verdict while a bounded run can still produce "
                             "one.\n")
            return bound
        if not told:
            sys.stderr.write(f"memgate: {name} has {have} MB, {what} needs {need} "
                             "MB — waiting for it to free memory\n")
            told = True
        sleep(15)
