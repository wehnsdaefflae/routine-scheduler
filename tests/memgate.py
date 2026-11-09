"""A test session WAITS for the host to have the memory it needs before it starts (2026-11-05).

This box has 14.7 GB of RAM, a swap that was full, and services whose peaks add up to more than
it holds. On 2026-11-05 a full browser gate started while the host had ~2 GB available: the
kernel's GLOBAL OOM killed a live routine run (grant-hunter:20261105-080003, rc=-9), two of the
gate's own ssh sessions, and an xdist worker mid-test. A gate is the one heavy load on this host
that can wait — nobody is watching it run, and a gate that starts ten minutes later costs ten
minutes, while one that starts now can cost a live run. So the controller process blocks in
`pytest_configure` until `MemAvailable` covers the selection, and gives up after
`RSCHED_TEST_MEM_WAIT_S` (default 20 min) with a non-zero exit naming both numbers: a refused
gate is retried, an OOM is not undone.

`/proc/meminfo` is the HOST's inside a container (no lxcfs here), which is exactly the number
that decides a global OOM — and the CONTAINER's own ceiling is a second, independent one: the
gate could be satisfied by a quiet host while its own cgroup sat near `memory.max` (accepted
proposal q-20261107-020004-53, 2026-11-09). So it waits for BOTH, the smaller deciding, and
says which one refused. The container figure is `memory.max` minus what the cgroup HOLDS
(`anon` + swapped pages) — never `memory.current`, which counts page cache the kernel drops at
the limit, and which read 96-100% on gates that killed nothing. Where it does not exist (not Linux), nothing waits. The needs are the
measured peaks of the selections: the fast suite at FAST_WORKERS workers (~250 MB each, plus the
controller), the browser suite at BROWSER_WORKERS (a Chromium, a uvicorn and a test each).
`RSCHED_TEST_MEM_WAIT=0` turns the wait off for a run someone is watching.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

#: MB of MemAvailable each selection needs before it may start.
NEED_MB = {"fast": 2048, "browser": 3584}

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


def wait_for_memory(selection: str, *, read=available_mb, read_cgroup=cgroup_headroom_mb,
                    sleep=time.sleep, clock=time.monotonic) -> None:
    """Block until the host can take `selection` ("fast" | "browser"), or exit the session."""
    if os.environ.get("RSCHED_TEST_MEM_WAIT", "") == "0":
        return
    need = NEED_MB[selection]
    deadline = clock() + float(os.environ.get("RSCHED_TEST_MEM_WAIT_S") or 1200)
    told = False
    while True:
        short = [(name, have) for name, have in (("host memory", read()),
                                                 ("this container's cgroup", read_cgroup()))
                 if have is not None and have < need]
        if not short:
            return
        name, have = min(short, key=lambda s: s[1])
        if clock() >= deadline:
            pytest.exit(f"{name}: {have} MB available, the {selection} suite needs {need} MB "
                        "— not started, so it cannot push it into an OOM that kills a live "
                        "run (tests/memgate.py). Retry when it is quieter.", returncode=3)
        if not told:
            sys.stderr.write(f"memgate: {name} has {have} MB, {selection} suite needs {need} "
                             "MB — waiting for it to free memory\n")
            told = True
        sleep(15)
