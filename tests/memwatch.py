"""BOUND a test session's hold instead of predicting it — admit, then watch (D295).

The operator, 2026-11-19, choosing between four shapes for the gate's admission floor:
*"b then a — build the RSS watchdog, and price the admission estimate from the selection's own
history"*.

## Why the constant could not do this job

`memgate.NEED_MB` charges a whole CLASS of selections one figure. Read back off the gate's own
`held_peak_bytes` rows, a serial test stage has held **500-4,393 MB (median 1,156) over n=255**,
so the 2,816 MB serial charge is ~2.4x the median AND 0.64x the worst: it refuses selections
that fit and admits one that does not. Three release gates on 2026-11-18 were refused with ZERO
tests after 1,201.6 s of waiting while the whole non-browser suite was measured running green
holding 1.2 GB. No single number prices a distribution that wide.

A WATCHDOG does not have to. The thing the wait exists to prevent is the KERNEL choosing an OOM
victim — on 2026-11-05 it chose a live routine run — and a watchdog prevents it by a stronger
means: it samples what the session actually holds and ENDS THE SESSION when the hold crosses the
cap, so an overrun costs the gate and never a live run. Nothing is predicted and no clock is
spent waiting for a figure that was wrong in both directions.

## The UNIT, which cost a release gate to get right

The hold is read from THIS CGROUP's `anon` + swapped pages — the same figure every
`memgate.NEED_MB` charge was measured in, and the figure a global OOM is decided on.

Two wrong units have now each cost real work, and they are the same mistake twice:

* **Virtual address space.** An `RLIMIT_AS` cap of 1,600 MB fabricated 164 identical "cannot
  start new thread" failures in a suite holding 450 MB (scheduler-builder, 2026-11-18).
* **A SUM of per-process RSS.** Each process's resident set includes the shared file-backed
  pages it maps — the interpreter, the libraries — so summing a controller and four xdist
  workers counts those five times over. Measured 2026-11-19 on gate
  `1795100129134213629-release`: the cgroup held 2,706 MB against a 3,328 MB cap, and the
  summed-RSS watchdog killed the stage anyway at **3,922 passed and zero failures**. A guard
  that ends a healthy release gate is worse than the over-charge it replaces.

The lesson both share: measure in the unit the budget was written in. The budgets here are
cgroup holds, so the bound is a cgroup hold.

One consequence to be explicit about: this cgroup holds the daemon and any live routine run too,
so the reading is not the test session alone. That is deliberate and matches how the charges were
derived — `memgate.NEED_MB` says in so many words that every figure there is "the whole CGROUP's
hold while the selection ran, a live routine run included — deliberately conservative, since what
must fit is everything at once". A bound in the same unit as the charge is the only one that can
be compared with it.

## What the wait does now that a bound exists

`memgate`'s wait used to EXIT 3 without starting when it ran out, on the reasoning that a
refused gate is retried while an OOM is not undone. The measured outcome of that was no verdict
at all: three release gates on 2026-11-18 refused after 1,201.6 s with ZERO tests, and the
browser stage on 2026-11-19 after 1,202 s waiting for 3,584 MB on a box holding 3,008 MB — while
the whole non-browser suite was measured running green at 1.2 GB. So the deadline now ADMITS the
session, bounded at what was really free, and this module ends it if the hold crosses that. The
protection the wait exists for is intact (the kernel still never chooses a live routine run),
and a tight box yields a verdict instead of nothing.

## What the cap is: what the session was ADMITTED for, never what is free

The cap is the selection's own priced need plus `SLACK_MB` — and the opposite reading, deriving
it from the memory that happens to be free, was caught here before it shipped. Measured on this
box at 15:34 on 2026-11-19 the host had 2,802 MB available while the fast stage is measured
holding 3.08-3.29 GB: a headroom-derived cap would have killed a legitimate release gate for
running at its ordinary cost, which is strictly worse than the over-charge this replaces.

So admission and bound are two halves of ONE figure. The session is admitted when its priced
need fits the memory that exists, and it is then held to that need: a session far past its own
measured history has gone wrong, and that is the only case worth killing. Where no figure can
be derived there is no cap and no watchdog, which is what "no ceiling to read" has always meant
here.

## The history, and what it is for

Part (a) of the operator's answer: the admission ESTIMATE for a selection comes from that
selection's own measured holds rather than its class's worst case. The gate's own
`memory-peaks.jsonl` cannot answer it — measured 2026-11-19, its 1,004 rows carry `stage` and
no selection identity at all, which is precisely why the `tests` label pools 754 rows spanning
500-4,393 MB. So the session records its OWN peak under a key naming what it ran
(`memhistory.selection_key`), and `memhistory.priced_for` reads that key's worst hold back. One
never seen before has no history and falls back to the class constant BY NAME, so a refusal can
always say which figure refused it.

Nothing here decides whether to wait: `memgate` owns that. This module answers "what may this
session hold" and "what has this selection held before", and stops a session that exceeds it.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from pathlib import Path

import memhistory

#: MB a session may hold ABOVE what it was admitted for before the watchdog ends it. The cap is
#: the admission figure plus this, so a selection that grew a little since its last run is not
#: killed for it — the figure the gate admitted it on already carries a 25 % margin, and this is
#: the allowance on top for the tree genuinely getting bigger. A session 512 MB past a priced
#: need has not grown; something has gone wrong in it.
SLACK_MB = 512

#: The cap never falls below this. A cap under a bare pytest controller's own hold (measured
#: 500-930 MB across the gate's serial stages) would kill every session at collection, turning
#: the guard into an outage — a refusal to START is the honest answer at that point, and that is
#: `memgate`'s decision, not this module's.
FLOOR_MB = 1024

#: This cgroup's own directory — the session's hold is read from it, in the unit every
#: `memgate.NEED_MB` figure was measured in. Same path `memgate.CGROUP` reads headroom from.
CGROUP = Path("/sys/fs/cgroup")

#: How often the hold is sampled. 0.25 s is what `scheduler-builder`'s own watchdog used over
#: whole-suite runs: fast enough that a worker's allocation spike is seen, cheap enough that the
#: sampler never shows up in the stage's own cost.
INTERVAL_S = 0.25

def cap_mb(need_mb: int | None, *, class_mb: int = 0, slack_mb: int = SLACK_MB,
           floor_mb: int = FLOOR_MB) -> int | None:
    """The MB this session may hold: the LARGER of its admission figure and its CLASS's worst
    case, plus a slack, never under the floor. None without an admission figure, which disables
    the watchdog.

    ## The asymmetry, and it cost a release gate to learn

    History may lower what a session is ADMITTED on. It may never lower the BOUND. Measured on
    gate `1795100129134213629-release` (2026-11-19), which this module killed at **3,922 passed
    and zero failures**: a 0.3-second session had recorded 68 MB under the same selection key, so
    the next run of that key was priced at 128 MB, capped at the 1,024 MB floor — and the real
    fast stage held 1,056 MB. Killed 32 MB over, for running at its ordinary cost.

    The two figures answer different questions and must be read differently. "May this start?"
    is answered optimistically, because the cost of being wrong is a wait. "Must this be
    stopped?" is answered conservatively, because the cost of being wrong is a lost gate — so
    the bound comes from the class's measured worst case, and only a session exceeding THAT has
    actually gone wrong. One unrepresentative sample can then make the gate quicker to start and
    can never make it quicker to die.

    The cap is also NOT the free headroom, which was the first wrong reading, caught before it
    shipped: at 15:34 that day the host had 2,802 MB available while the fast stage is measured
    holding 3.08-3.29 GB, so a headroom-derived cap would have killed a healthy gate too.
    """
    if need_mb is None:
        return None
    return max(int(floor_mb), max(int(need_mb), int(class_mb)) + int(slack_mb))


def cgroup_hold_mb(_pid: int = 0, cgroup: Path = CGROUP) -> float:
    """What THIS CGROUP holds right now, in MB: anon pages plus swapped ones.

    The whole session — controller and every xdist worker — lives in one cgroup, so this is the
    session's hold with no summing and therefore no double-counting of the shared pages a sum
    gets wrong (see the unit section above: a summed-RSS reading killed a green gate). It is
    also exactly the figure `memgate.cgroup_headroom_mb` subtracts from `memory.max`, so the
    bound and the admission charge are expressed in one unit and cannot disagree.

    `_pid` is accepted and ignored: the `Watchdog` hands its reader a pid, and a future reader
    that really is per-process should not have to change the call. 0.0 where there is no cgroup
    to read, which switches the watchdog off exactly as a missing admission figure does.
    """
    try:
        stat = (cgroup / "memory.stat").read_text(encoding="ascii")
    except OSError:
        return 0.0
    anon = next((int(line.split()[1]) for line in stat.splitlines()
                 if line.startswith("anon ")), None)
    if anon is None:
        return 0.0
    try:
        swap = int((cgroup / "memory.swap.current").read_text(encoding="ascii"))
    except (OSError, ValueError):
        swap = 0
    return (anon + swap) / 1048576.0


class Watchdog:
    """A thread that samples the session's hold and ENDS THE SESSION on an overrun.

    The kill is a SIGINT to the session's own process — pytest answers it by shutting its
    workers down and reporting, so an overrun arrives as a readable stop rather than as a
    process that vanished. It is injectable (`kill`) for the same reason the readers in
    `memgate` are: a test pinning this behaviour must not have to be killed to do it.
    """

    def __init__(self, *, cap: int, pid: int | None = None, interval_s: float = INTERVAL_S,
                 selection: str = "", key: str = "", evidence_dir: Path | None = None,
                 read=cgroup_hold_mb, kill=os.kill, clock=time.monotonic,
                 sleep=time.sleep, warn=None) -> None:
        self.cap = int(cap)
        self.pid = int(pid if pid is not None else os.getpid())
        self.interval_s = float(interval_s)
        #: What this session is, for the history row `finish` writes.
        self.selection, self.key = str(selection), str(key)
        self.evidence_dir = evidence_dir
        self._read, self._kill, self._clock, self._sleep = read, kill, clock, sleep
        self._warn = warn or sys.stderr.write
        self.peak_mb = 0.0
        self.killed_at_mb: float | None = None
        self.started_at = self._clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self) -> float:
        """Take ONE reading, remember the peak, and kill the session if it is over the cap.

        The whole decision in one call, so a test can drive it without a thread and the threaded
        path is only a loop around it.
        """
        held = float(self._read(self.pid))
        self.peak_mb = max(self.peak_mb, held)
        if held > self.cap and self.killed_at_mb is None:
            self.killed_at_mb = held
            self._warn(f"memwatch: this session holds {held:.0f} MB, over its {self.cap} MB "
                       "cap — ENDING IT so the kernel does not choose an OOM victim instead "
                       "(tests/memwatch.py). Retry with a narrower selection or on a quieter "
                       "box.\n")
            self._kill(self.pid, signal.SIGINT)
        return held

    def start(self) -> Watchdog:
        """Begin sampling in a daemon thread, so a session that ends first is never held open."""
        self._thread = threading.Thread(target=self._loop, name="memwatch", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._sleep(self.interval_s)

    def stop(self) -> float:
        """Stop sampling and return the peak hold in MB — what gets recorded as the history."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return self.peak_mb

    @property
    def seconds(self) -> float:
        """How long this session has been watched."""
        return self._clock() - self.started_at


#: The session's live watchdog — a ONE-SLOT list, because a pytest session IS one process: the
#: controller arms it from `pytest_configure` and the same process records it at exit, and there
#: is exactly one of each per session. A list rather than a rebound module name so neither
#: function needs a `global` statement to reach it.
_ARMED: list[Watchdog] = []


def arm(*, cap: int | None, selection: str, key: str,
        evidence_dir: Path, pid: int | None = None) -> Watchdog | None:
    """Start watching this session, remembering what to record when it ends.

    None = not armed, which happens when no cap could be derived (`cap_mb` answered None, i.e.
    the box has no memory reader at all): there is no ceiling to enforce, so enforcing one
    would be inventing it.
    """
    if cap is None:
        return None
    _ARMED[:] = [Watchdog(cap=int(cap), pid=pid, selection=selection, key=key,
                          evidence_dir=evidence_dir).start()]
    # The hold is recorded at PROCESS EXIT rather than from a pytest hook, and that is the
    # second design the evidence corrected. A `pytest_unconfigure` would have to be registered
    # from somewhere: `conftest.py` is at its size ceiling and may only shrink, and registering
    # this module on `config.pluginmanager` broke two existing pins outright — their config
    # stubs carry a bare `object()` there, which exists so `should_wait` can TEST for the
    # attribute and can never be CALLED. `atexit` needs neither, runs once per session in the
    # controller that armed the watchdog, and still fires when the session is killed by the
    # SIGINT this watchdog sends.
    atexit.register(finish)
    return _ARMED[0]


def finish() -> int | None:
    """Stop the armed watchdog and RECORD what the session held, in MB — the history part (a)
    prices the next run of this selection from. None when nothing was armed.

    Recorded even when the watchdog ENDED the session: a selection that overran its cap is
    exactly the one whose next admission must be priced from the attempt, and the peak is the
    best lower bound on its real cost that exists.
    """
    if not _ARMED:
        return None
    watchdog = _ARMED.pop()
    held = int(watchdog.stop())
    if watchdog.evidence_dir is not None:
        memhistory.record_hold(watchdog.evidence_dir, key=watchdog.key, held_mb=held,
                    selection=watchdog.selection, seconds=watchdog.seconds)
    return held
