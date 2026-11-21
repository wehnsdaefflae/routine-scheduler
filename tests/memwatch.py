"""BOUND a test session's hold instead of predicting it — admit, then watch (D295)."""

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
    """
    if need_mb is None:
        return None
    return max(int(floor_mb), max(int(need_mb), int(class_mb)) + int(slack_mb))


def cgroup_hold_mb(_pid: int = 0, cgroup: Path = CGROUP) -> float:
    """What THIS CGROUP holds right now, in MB: anon pages plus swapped ones."""
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
    """A thread that samples the session's hold and ENDS THE SESSION on an overrun."""

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
        """Take ONE reading, remember the peak, and kill the session if it is over the cap."""
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
    """Start watching this session, remembering what to record when it ends."""
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
    """
    if not _ARMED:
        return None
    watchdog = _ARMED.pop()
    held = int(watchdog.stop())
    if watchdog.evidence_dir is not None:
        memhistory.record_hold(watchdog.evidence_dir, key=watchdog.key, held_mb=held,
                    selection=watchdog.selection, seconds=watchdog.seconds)
    return held
