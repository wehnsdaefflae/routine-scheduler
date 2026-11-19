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

RSS is the unit, deliberately. Resident pages are what compete for host memory and what a global
OOM is decided on. Bounding VIRTUAL address space instead is the trap this instance has already
paid for: an `RLIMIT_AS` cap of 1,600 MB fabricated 164 identical "cannot start new thread"
failures in a suite holding 450 MB (scheduler-builder, 2026-11-18).

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
(`selection_key`), and `priced_need_mb` reads that key's worst previous hold back. A selection
never seen before has no history and falls back to the class constant BY NAME, so a refusal can
always say which figure refused it.

Nothing here decides whether to wait: `memgate` owns that. This module answers "what may this
session hold" and "what has this selection held before", and stops a session that exceeds it.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import sys
import threading
import time
from hashlib import sha1
from pathlib import Path

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

#: How often the hold is sampled. 0.25 s is what `scheduler-builder`'s own watchdog used over
#: whole-suite runs: fast enough that a worker's allocation spike is seen, cheap enough that the
#: sampler never shows up in the stage's own cost.
INTERVAL_S = 0.25

#: Where a selection's measured holds are kept — beside the gate's own evidence, since it is
#: evidence of the same kind. One JSON line per finished session.
HISTORY_FILE = "selection-holds.jsonl"

#: The directory that evidence lives in, derived like `test_policy`'s warn-window file: this
#: checkout's own `.audit-wt/.gate-evidence`, so a worktree records against the tree it tested.
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / ".audit-wt" / ".gate-evidence"


def priced_for(key: str, fallback_mb: int, *, evidence_dir: Path | None = None,
               ) -> tuple[int, str]:
    """The charge for the selection `key` names, and WHICH figure it was — D295's part (a).

    The history read and the pricing in one call, because the two are never wanted apart: a
    caller that read the file and then priced from it would have to know this module's row
    shape. `fallback_mb` is the CLASS charge its caller computed (`memgate.need_mb`), passed in
    rather than imported — the class figures are the gate's subject and the history is this
    module's, and a cycle between the two is the only way they could disagree.
    """
    return priced_need_mb(key, history=holds_by_key(evidence_dir or EVIDENCE_DIR),
                          fallback_mb=fallback_mb)


def selection_key(paths, markexpr: str, workers: int) -> str:
    """A stable name for WHAT THIS SESSION RUNS — the thing a hold belongs to.

    The selection, its marker expression and its worker count, because all three change the
    hold: the same paths at 4 xdist workers hold about 2.4 GB more than at none. Sorted, so the
    order the paths were typed in does not mint a second key for one selection, and digested
    rather than spelled out because a key is compared, never read — a whole-suite call's paths
    would otherwise be a 4,000-character field in every row.
    """
    body = "\0".join([*sorted(str(p) for p in (paths or [])), markexpr.strip(),
                      f"w{max(0, int(workers))}"])
    return sha1(body.encode("utf-8", "replace")).hexdigest()[:16]


def history_path(evidence_dir: Path) -> Path:
    """The history file's path under a gate-evidence directory."""
    return Path(evidence_dir) / HISTORY_FILE


def record_hold(evidence_dir: Path, *, key: str, held_mb: int, selection: str,
                seconds: float) -> None:
    """Append ONE session's measured peak hold. Best-effort: a guard that can fail the session
    it is guarding is a defect dressed as evidence, so every write error is swallowed.

    `selection` and `seconds` ride along for a human reading the file; only `key` and `held_mb`
    are read back by `priced_need_mb`.
    """
    try:
        path = history_path(evidence_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key,
                                     "selection": selection, "held_mb": int(held_mb),
                                     "seconds": round(float(seconds), 1)}) + "\n")
    except (OSError, TypeError, ValueError):
        return


def holds_by_key(evidence_dir: Path) -> dict[str, int]:
    """The WORST hold recorded per selection key. An unreadable or half-written line is
    skipped rather than raised on: this file is appended to by sessions that can be killed.
    """
    worst: dict[str, int] = {}
    try:
        text = history_path(evidence_dir).read_text(encoding="utf-8")
    except OSError:
        return worst
    for line in text.splitlines():
        try:
            row = json.loads(line)
            key, held = str(row["key"]), int(row["held_mb"])
        except (ValueError, TypeError, KeyError):
            continue
        if held > worst.get(key, 0):
            worst[key] = held
    return worst


def priced_need_mb(key: str, *, history: dict[str, int], fallback_mb: int,
                   margin: float = 1.25) -> tuple[int, str]:
    """What to charge this selection, and WHICH figure that was — `(mb, source)`.

    Its own worst measured hold plus a margin (25 %) when this exact selection has run before;
    the class constant otherwise, named as the fallback so a refusal says which figure refused
    it. The margin exists because a hold is not a constant either: the same selection grows with
    the tree, and pricing it at exactly its last peak would refuse it the first time a new test
    lands.

    Never MORE than the class constant: that figure is the measured worst case of a whole class
    and a per-selection price above it would re-introduce the over-charge this replaces.
    """
    seen = int(history.get(str(key), 0))
    if seen <= 0:
        return int(fallback_mb), "the class constant (this selection has not run before)"
    priced = min(int(fallback_mb), -(-int(seen * float(margin)) // 64) * 64)
    return priced, f"this selection's own worst hold ({seen} MB) plus a margin"


def cap_mb(need_mb: int | None, *, slack_mb: int = SLACK_MB,
           floor_mb: int = FLOOR_MB) -> int | None:
    """The MB this session may hold — WHAT IT WAS ADMITTED FOR, plus a slack, never under the
    floor. None when there is no admission figure at all, which disables the watchdog.

    The cap is the admission figure and NOT the free headroom, and getting that backwards was
    caught here before it shipped: on this box at 15:34 on 2026-11-19 the host had 2,802 MB
    available, so a headroom-derived cap would have been ~2.0 GB — and the fast stage is
    MEASURED holding 3.08-3.29 GB. The watchdog would have killed a legitimate release gate
    for running at its normal cost, which is a worse failure than the over-charge it replaces.

    Admission and bound are therefore two halves of ONE figure: the session is admitted only
    when its priced need fits in the memory that exists, and it is then held to that need. A
    selection holding far more than its own measured history has gone wrong — that is the case
    worth killing, and it is the only one this cap kills.
    """
    if need_mb is None:
        return None
    return max(int(floor_mb), int(need_mb) + int(slack_mb))


def tree_rss_mb(pid: int) -> float:
    """The RESIDENT hold of this process and every descendant, in MB.

    Read from `/proc/<pid>/statm` (field 2 is resident pages) over the process's own children,
    because an xdist session's cost is its WORKERS: the controller alone holds under a gigabyte
    while four workers add about 600 MB each. A process that exits mid-walk is skipped — a dead
    child holds nothing.
    """
    total = 0.0
    for target in (pid, *_children(pid)):
        try:
            with open(f"/proc/{target}/statm", encoding="utf-8") as handle:
                total += int(handle.read().split()[1]) * 4096 / 1048576.0
        except (OSError, IndexError, ValueError):
            continue
    return total


def _children(pid: int) -> list[int]:
    """Every descendant pid, found through `/proc/<pid>/task/*/children`."""
    found: list[int] = []
    frontier = [int(pid)]
    while frontier:
        parent = frontier.pop()
        try:
            tasks = list((Path("/proc") / str(parent) / "task").iterdir())
        except OSError:
            continue
        for task in tasks:
            try:
                kids = [int(p) for p in (task / "children").read_text().split()]
            except (OSError, ValueError):
                continue
            for kid in kids:
                if kid not in found:
                    found.append(kid)
                    frontier.append(kid)
    return found


class Watchdog:
    """A thread that samples the session's hold and ENDS THE SESSION on an overrun.

    The kill is a SIGINT to the session's own process — pytest answers it by shutting its
    workers down and reporting, so an overrun arrives as a readable stop rather than as a
    process that vanished. It is injectable (`kill`) for the same reason the readers in
    `memgate` are: a test pinning this behaviour must not have to be killed to do it.
    """

    def __init__(self, *, cap: int, pid: int | None = None, interval_s: float = INTERVAL_S,
                 selection: str = "", key: str = "", evidence_dir: Path | None = None,
                 read=tree_rss_mb, kill=os.kill, clock=time.monotonic,
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


#: The session's live watchdog, or None when nothing is being watched. Module state because a
#: pytest session IS one process: the controller arms it in `pytest_configure` and the same
#: process stops it in `pytest_unconfigure`, and there is exactly one of each per session.
_ARMED: Watchdog | None = None


def arm(*, cap: int | None, selection: str, key: str,
        evidence_dir: Path, pid: int | None = None) -> Watchdog | None:
    """Start watching this session, remembering what to record when it ends.

    None = not armed, which happens when no cap could be derived (`cap_mb` answered None, i.e.
    the box has no memory reader at all): there is no ceiling to enforce, so enforcing one
    would be inventing it.
    """
    global _ARMED
    if cap is None:
        return None
    _ARMED = Watchdog(cap=int(cap), pid=pid, selection=selection, key=key,
                      evidence_dir=evidence_dir).start()
    # The hold is recorded at PROCESS EXIT rather than from a pytest hook, and that is the
    # second design the evidence corrected. A `pytest_unconfigure` would have to be registered
    # from somewhere: `conftest.py` is at its size ceiling and may only shrink, and registering
    # this module on `config.pluginmanager` broke two existing pins outright — their config
    # stubs carry a bare `object()` there, which exists so `should_wait` can TEST for the
    # attribute and can never be CALLED. `atexit` needs neither, runs once per session in the
    # controller that armed the watchdog, and still fires when the session is killed by the
    # SIGINT this watchdog sends.
    atexit.register(finish)
    return _ARMED


def finish() -> int | None:
    """Stop the armed watchdog and RECORD what the session held, in MB — the history part (a)
    prices the next run of this selection from. None when nothing was armed.

    Recorded even when the watchdog ENDED the session: a selection that overran its cap is
    exactly the one whose next admission must be priced from the attempt, and the peak is the
    best lower bound on its real cost that exists.
    """
    global _ARMED
    watchdog, _ARMED = _ARMED, None
    if watchdog is None:
        return None
    held = int(watchdog.stop())
    if watchdog.evidence_dir is not None:
        record_hold(watchdog.evidence_dir, key=watchdog.key, held_mb=held,
                    selection=watchdog.selection, seconds=watchdog.seconds)
    return held
