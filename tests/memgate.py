"""A test session WAITS for the host to have the memory it needs before it starts (2026-11-05).

This box has 14.7 GB of RAM, a swap that was full, and services whose peaks add up to more than
it holds. On 2026-11-05 a full browser gate started while the host had ~2 GB available: the
kernel's GLOBAL OOM killed a live routine run (grant-hunter:20261105-080003, rc=-9), two of the
gate's own ssh sessions, and an xdist worker mid-test. A gate is the one heavy load on this host
that can wait — nobody is watching it run, and a gate that starts ten minutes later costs ten
minutes, while one that starts now can cost a live run. So the controller process blocks in
`pytest_configure` until `MemAvailable` covers the selection.

D295 changed two things, and `tests/memwatch.py` holds the measurements: running out of wait
ADMITS the session under a bound that module enforces rather than refusing it, and the charge
comes from the selection's own history (`tests/memhistory.py`), the table below being the
fallback for a selection nobody has measured yet.

`/proc/meminfo` is the HOST's inside a container (no lxcfs here), which is exactly the number
that decides a global OOM — and the CONTAINER's own ceiling is a second, independent one: the
gate could be satisfied by a quiet host while its own cgroup sat near `memory.max` (accepted
proposal q-20261107-020004-53, 2026-11-09). So it waits for BOTH, the smaller deciding, and
says which one refused. The container figure is `memory.max` minus what the cgroup HOLDS
(`anon` + swapped pages) — never `memory.current`, which counts page cache the kernel drops at
the limit, and which read 96-100% on gates that killed nothing. Where it does not exist (not
Linux), nothing waits. The needs are the
measured HOLD of the selections, read off the gate's own `held_peak_bytes` rows rather than
estimated: the fast suite at FAST_WORKERS workers (~600 MB each, plus a ~900 MB controller),
the browser suite at BROWSER_WORKERS in its own container (a Chromium, a uvicorn and a test
each). `NEED_MB`'s comment holds the table and the dates.
`RSCHED_TEST_MEM_WAIT=0` turns the wait off for a run someone is watching.
"""

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

    D291 reduced the WORKER COUNT against the cgroup alone while the wait kept guarding both,
    so on a box whose HOST is the short side the count stayed at its ceiling and the session
    waited out `RSCHED_TEST_MEM_WAIT_S` and exited 3 — the refusal that decision replaced,
    surviving wherever the host binds first. Measured 2026-11-15 on this box: cgroup headroom
    3,845 MB, host MemAvailable 2,797 MB, four workers kept, `fast` refused after 20 minutes
    while a three-worker selection (2,816 MB) was within reach. One reading for the count and
    the wait, for the reason `need_mb` gives: the two must not be able to disagree.

    None only when NEITHER reader answers, which is what "no ceiling to read" has always meant.
    Both readers are resolved at CALL time off this module, never bound as defaults, so a test
    that monkeypatches either one reaches the code it is pinning (D291's own wiring pin does).
    """
    readers = (read or available_mb, read_cgroup or globals()["cgroup_headroom_mb"])
    have = [mb for mb in (reader() for reader in readers) if mb is not None]
    return min(have) if have else None


def should_wait(config) -> bool:
    """Is this the CONTROLLER of a real gate session — the one process that may block?

    Two things must be true, and the second was missing until 0.525.4. A worker is admitted
    without waiting (it carries `workerinput`; the controller already waited for the whole
    selection). And the config must be a REAL pytest one: a unit test that calls the
    `pytest_configure` hook directly to pin its distribution logic passes a stub with an
    `option` namespace and nothing else, and a stub has no `workerinput` either — so it fell
    into the controller branch and entered a twenty-minute memory wait inside a test.

    MEASURED, because it cost a whole release gate (2026-11-10, gate
    `1794268634309389266-release`): `tests/test_ui_harness.py` calls the hook three times with
    such a stub. Two of those selections ask for the BROWSER need, and on a box already running
    a 4-worker fast stage the cgroup headroom is below it — so both nodes blocked for exactly
    the 300 s pytest timeout, were marked `rerun`, restarted, and the stage never reported. The
    gate holds `/tmp/rsched-gates.lock` while that happens, so the hang is not local to one
    gate: every other routine's gate on this box waits behind it, for up to
    `RSCHED_TEST_MEM_WAIT_S` (20 min by default).
    It was invisible until then because the same three nodes pass in 0.0 s on a quiet box: the
    wait returns at once when the memory is there. A gate hang that appears only under load
    appears only when the gate matters.

    `pluginmanager` is the discriminator rather than a type check: every real `Config` has one
    from its constructor, no stub in this suite does, and it stays true for a config a future
    plugin wraps.
    """
    return not hasattr(config, "workerinput") and hasattr(config, "pluginmanager")


def wait_for_config(config, markexpr: str) -> None:
    """Wait for what THIS session will really start — the selection and its worker COUNT (D291).

    Here rather than in `conftest.py` because the charge is this module's subject and that file
    is over the house size bar, so it may only shrink: one call there, the reasoning here.

    By the time `pytest_configure` runs, `-n auto` is already the resolved integer (measured on
    this box, pytest 9.1.1 / xdist 3.8.0 — the same reading `selection_for` prices by), so the
    count the auto hook settled on against the cgroup headroom is what the wait is charged for.
    Charging the CEILING instead is what refused a gate that would have run perfectly well
    beside a live routine run, and refused it in a way that read as a flaky failure.

    Since D295 the admitted session is also BOUNDED, by `memwatch`.
    """
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

    `workers` is the count xdist ACTUALLY resolved, so the charge follows the selection that
    will run rather than the ceiling it might have run at (D291). Zero means "price it by the
    constant", which is what every non-fast selection does.

    `key` names the selection for D295's per-selection pricing: given one, the charge is what
    THIS selection has held before rather than its class's worst case, and the refusal says
    which of the two figures it was. No key keeps the class charge, so every existing caller —
    including the tests that pin the old behaviour — is unaffected.
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
