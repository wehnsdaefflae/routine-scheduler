"""A test session's MEMORY ADMISSION: it waits for room, runs no more workers than fit, and is
ended over its cap — so the kernel never chooses a live routine run as an OOM victim instead.

Three things, and only these (C7, the operator 2026-11-21, which dropped the measured-history
pricing this module used to carry beside them):

1. **Wait.** The controller of a real session blocks until BOTH the host's `MemAvailable` and
   this container's own cgroup headroom cover its selection's charge (`NEED_MB`, the fast one
   scaled by the xdist worker count). On 2026-11-05 a gate started on a host with ~2 GB free and
   the global OOM killed a live run. Past `RSCHED_TEST_MEM_WAIT_S` it STARTS ANYWAY under the
   watchdog's cap (D295: refusing guaranteed no verdict — three gates exited 3 after 1,201 s
   with zero tests on 2026-11-18 — while a bounded run can still produce one).
   `RSCHED_TEST_MEM_WAIT=0` switches the wait and the watchdog off.
2. **Cap the workers.** `headroom_mb` is the one reading `browsersource.fast_workers` sizes
   `-n auto` from (D291: beside a live run the gate runs SMALLER instead of being refused).
3. **Kill on a breach.** A `Watchdog` thread samples the cgroup's hold (anon + swap, the figure
   an OOM is decided on) and SIGINTs the session once it passes its cap.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

MEMINFO = Path("/proc/meminfo")
CGROUP = Path("/sys/fs/cgroup")

#: MB each selection must find free before it starts — the WHOLE cgroup's measured hold while it
#: ran (`held_peak_bytes` in `.gate-evidence/memory-peaks.jsonl`: anon + swap, never page cache),
#: rounded up to the next 256 MB on the MB figure:
#:
#:     selection   measured hold                                  charge
#:     fast        3.08-3.29 GB, n=7, controller + 4 workers      3584 (worst 3,367)
#:     serial      876-2700 MB, n=21, `-n 0`                      2816 (worst 2,700)
#:     browser     1797-1876 MB, n=5, uitest's own 4 GiB cgroup   2048 (submit.py's peak)
#:
#: `browser` is measured in ANOTHER cgroup: `deploy/uitest/submit.py` hands the job to the
#: `uitest` container, so there this module reads that container's cgroup (see `READERS_FOR`).
#: The worker count did not move for these figures: `memory.current` reads 96-100 % on fast
#: stages because it counts reclaimable cache, while the deciding figure sits at 61.6-65.8 %.
NEED_MB = {"fast": 3584, "browser": 2048, "serial": 2816}

#: The fast charge is DERIVED from the worker count (D291). The fast rows' median increment over a
#: bare controller is 2.25 GB (2.38 GB at worst) — the WORKERS, 2.38 GB / 4 = 610 MB each — and
#: 930 + 4 x 610 = 3,370 reproduces the worst four-worker hold's rounding to 3,584 exactly.
CONTROLLER_HOLD_MB = 930
PER_WORKER_HOLD_MB = 610

#: What each selection is called in the wait's message — a "fast suite" named on a two-file
#: `-n 0` call read as a memory leak and sent R3376's author looking for one.
SELECTION_PROSE = {
    "fast": "the fast suite at 4 xdist workers",
    "browser": "the browser suite",
    "serial": "this serial selection (no xdist workers)",
}

#: Which readings bound a selection. Everything in THIS container is bound by host and cgroup
#: alike; the browser stage runs inside `uitest`, whose `mem_limit` bounds it whatever the host's
#: reclaimable total — measured 2026-11-19, its cgroup had 4,040 MB of headroom while the host
#: read 2,413 MB, so reading the host refused a stage with room to spare.
READERS_FOR = {"browser": ("this container's cgroup",)}

#: MB a session may hold above its charge before the watchdog ends it: the charge already carries
#: the rounding, this is the allowance for the tree genuinely growing since it was measured.
SLACK_MB = 512

#: How often the watchdog samples — fast enough to see a worker's spike, too cheap to show up.
INTERVAL_S = 0.25


def fast_need_mb(workers: int) -> int:
    """A fast selection's charge at `workers` xdist workers, rounded UP to 256 MB — never below
    the `serial` charge it contains (a serial row was measured holding 2,700 MB)."""
    raw = CONTROLLER_HOLD_MB + max(0, int(workers)) * PER_WORKER_HOLD_MB
    return max(-(-raw // 256) * 256, NEED_MB["serial"])


def need_mb(selection: str, workers: int = 0) -> int:
    """The charge for one selection — one reading, so the wait and its message cannot disagree."""
    if selection == "fast" and workers:
        return fast_need_mb(workers)
    return NEED_MB[selection]


def prose_for(selection: str, workers: int = 0) -> str:
    """How the wait NAMES what it waits for, with the worker count it settled on (D291)."""
    if selection == "fast" and workers:
        return f"the fast suite at {workers} xdist workers"
    return SELECTION_PROSE.get(selection, f"the {selection} suite")


def selection_for(config, markexpr: str) -> str:
    """Which charge this session pays — by what it will really start."""
    if "not ui" not in markexpr:
        return "browser"
    serial = (getattr(config.option, "dist", "no") == "no"
              or getattr(config.option, "numprocesses", None) in (0, None))
    return "serial" if serial else "fast"


def available_mb(meminfo: Path = MEMINFO) -> int | None:
    """The host's MemAvailable in MB, or None where it cannot be read."""
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _held_bytes(cgroup: Path) -> int | None:
    """Anon plus swapped bytes this cgroup holds — what an OOM is decided on, never the page
    cache `memory.current` also counts. None without a `memory.stat` to read."""
    try:
        stat = (cgroup / "memory.stat").read_text(encoding="ascii")
    except OSError:
        return None
    anon = next((int(line.split()[1]) for line in stat.splitlines()
                 if line.startswith("anon ")), None)
    if anon is None:
        return None
    try:
        swap = int((cgroup / "memory.swap.current").read_text(encoding="ascii"))
    except (OSError, ValueError):
        swap = 0
    return anon + swap


def cgroup_headroom_mb(cgroup: Path = CGROUP) -> int | None:
    """MB this cgroup can still HOLD before its `memory.max`, or None where there is no ceiling
    to read (the host itself, cgroup v1, `max`)."""
    try:
        limit = (cgroup / "memory.max").read_text(encoding="ascii").strip()
    except OSError:
        return None
    held = _held_bytes(cgroup)
    if not limit.isdigit() or held is None:
        return None
    return (int(limit) - held) // (1024 * 1024)


def cgroup_hold_mb(_pid: int = 0, cgroup: Path = CGROUP) -> float:
    """What this cgroup holds right now in MB — the watchdog's reading; 0.0 where there is no
    cgroup, which is below every cap, so an unreadable box never ends a session."""
    return (_held_bytes(cgroup) or 0) / 1048576.0


def headroom_mb(*, read=None, read_cgroup=None) -> int | None:
    """The MB a selection may use: the SMALLER of host MemAvailable and the cgroup's headroom —
    the pair the wait enforces, so the worker count and the wait cannot disagree."""
    readers = (read or available_mb, read_cgroup or globals()["cgroup_headroom_mb"])
    have = [mb for mb in (reader() for reader in readers) if mb is not None]
    return min(have) if have else None


def readings_for(selection: str, read, read_cgroup) -> tuple[tuple[str, int | None], ...]:
    """The (name, MB) readings that may hold `selection` back, in the order the wait names them."""
    every = (("host memory", read), ("this container's cgroup", read_cgroup))
    allowed = READERS_FOR.get(selection)
    return tuple((name, reader()) for name, reader in every
                 if allowed is None or name in allowed)


def should_wait(config) -> bool:
    """Is this the CONTROLLER of a real session — the one process that may block? A worker
    carries `workerinput`; a unit test's config stub has no `pluginmanager` (two such stubs once
    blocked a fast stage for the 300 s timeout while holding the shared gate lock)."""
    return not hasattr(config, "workerinput") and hasattr(config, "pluginmanager")


def wait_for_config(config, markexpr: str) -> None:
    """Wait for what THIS session will really start, then watch it: the cap is the charge, or
    what the deadline admitted when that is larger, plus `SLACK_MB` — never the memory that
    happens to be free, which would kill a fast stage (3.08-3.29 GB) on a box with 2.8 GB free."""
    selection = selection_for(config, markexpr)
    workers = getattr(config.option, "numprocesses", 0) or 0
    admitted = wait_for_memory(selection, workers)
    if admitted is not None:
        Watchdog(cap=max(admitted, need_mb(selection, workers)) + SLACK_MB).start()


def wait_for_memory(selection: str, workers: int = 0, *, read=available_mb,
                    read_cgroup=cgroup_headroom_mb, read_hold=None, sleep=time.sleep,
                    clock=time.monotonic) -> int | None:
    """Block until `selection` fits, then say what it was admitted at in MB of CGROUP HOLD: its
    charge, or — past the deadline — what was free PLUS what the cgroup already holds (the unit
    the watchdog reads; the free MB raw would sit below the daemon's own ~1.3 GB and kill the
    session at collection). None when the wait is switched off."""
    if os.environ.get("RSCHED_TEST_MEM_WAIT", "") == "0":
        return None
    need, what = need_mb(selection, workers), prose_for(selection, workers)
    deadline = clock() + float(os.environ.get("RSCHED_TEST_MEM_WAIT_S") or 1200)
    told = False
    while True:
        short = [(name, have) for name, have in readings_for(selection, read, read_cgroup)
                 if have is not None and have < need]
        if not short:
            return need
        name, have = min(short, key=lambda s: s[1])
        if clock() >= deadline:
            bound = int(have) + int((read_hold or cgroup_hold_mb)())
            sys.stderr.write(f"memgate: {name} still has only {have} MB and {what} wants "
                             f"{need} MB — STARTING IT ANYWAY, bounded at {bound} MB of cgroup "
                             "hold by the watchdog (tests/memgate.py), because refusing "
                             "guarantees no verdict while a bounded run can still produce "
                             "one.\n")
            return bound
        if not told:
            sys.stderr.write(f"memgate: {name} has {have} MB, {what} needs {need} "
                             "MB — waiting for it to free memory\n")
            told = True
        sleep(15)


class Watchdog:
    """A daemon thread that samples the cgroup's hold and ENDS THE SESSION over its cap."""

    def __init__(self, *, cap: int, pid: int | None = None, read=cgroup_hold_mb,
                 kill=os.kill, sleep=time.sleep, warn=None) -> None:
        self.cap = int(cap)
        self.pid = int(pid if pid is not None else os.getpid())
        self._read, self._kill, self._sleep = read, kill, sleep
        self._warn = warn or sys.stderr.write
        self.killed_at_mb: float | None = None

    def sample(self) -> float:
        """Take ONE reading and SIGINT the session the first time it is over the cap — SIGINT so
        pytest stops its workers and still reports; once, so an overrun is not a signal storm."""
        held = float(self._read(self.pid))
        if held > self.cap and self.killed_at_mb is None:
            self.killed_at_mb = held
            self._warn(f"memgate: this session holds {held:.0f} MB, over its {self.cap} MB "
                       "cap — ENDING IT so the kernel does not choose an OOM victim instead "
                       "(tests/memgate.py). Retry with a narrower selection or on a quieter "
                       "box.\n")
            self._kill(self.pid, signal.SIGINT)
        return held

    def start(self) -> Watchdog:
        """Sample in a DAEMON thread, so a session that ends first is never held open by it."""
        threading.Thread(target=self._loop, name="memgate-watchdog", daemon=True).start()
        return self

    def _loop(self) -> None:
        while True:
            self.sample()
            self._sleep(INTERVAL_S)
