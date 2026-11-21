"""Kept chip verdicts for files-card rows on a NETWORK share — checked off the request (2026-11-21).

`/api/runs/{id}/files` was the console's slowest route by server time: 1,070 rows over 2 s in
13 days, median 6.0 s, worst 87.6 s. Timed against the runs behind most of them, every second
was the D172 predicate (`servable.servable_rel`) resolving and statting rows on the NAS share
over sshfs — 60.9 s for a 318-row card, 73.7 s for a 2,056-row one, up to 5 s for one row,
with F896's 16-thread pool barely helping because the share answers one request at a time.

So a row on a network mount (`web/netmounts`) is no longer checked inside the request. Its
verdict is KEPT here, per run and per row, with the time it was taken, and a background job
refreshes it while someone is looking at that run. Local rows are still checked fresh on every
request, which is what F896's freshness tests (a deleted file, a revoked grant, a retargeted
symlink) pin, and which costs microseconds.

The kept verdict decides only whether the card OFFERS a click. The route behind the click,
`api_runs.run_file`, still resolves the roots from the live `routine.yaml` and proves
containment on the opened descriptor on every call, so a revoked grant stops SERVING at once,
as D172 requires; what can lag, by at most `VERDICT_MAX_AGE_S`, is the chip.

UNKNOWN IS NEVER SERVABLE. A row with no verdict yet, one whose check raised, or one older than
`VERDICT_MAX_AGE_S` (a share that stopped answering holds the job's thread, and no second job
starts beside it) reads as `None`, and the card renders no click for it. `pending` says a job is
running for rows still unknown, so the card fetches again until they land.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

from . import netmounts
from .filechecks import FileChecks
from .servable import granted_roots, roots_for_run

log = logging.getLogger("rsched.web")

#: A verdict younger than this is served without a re-check: a share's files move slowly, and
#: one re-check of a large card is a minute of NAS traffic.
REFRESH_AFTER_S = 300.0

#: Past this a verdict is not served at all — three missed refreshes means the share is not
#: answering this process, and that is UNKNOWN, not the last answer.
VERDICT_MAX_AGE_S = 900.0

#: Least time between the end of one job for a run and the start of the next. A job that could
#: not check its rows leaves them unknown, which makes a refresh due at once; without this gap a
#: card left open would restart a failing job on every poll.
MIN_GAP_S = 30.0

#: Rows per call into F896's pool: verdicts land in this many at a time, and a row that raises
#: costs only its own chunk.
CHUNK = 32

#: Runs whose verdicts are kept at once (least recently viewed evicted). A run is only checked
#: while someone has its card open, so this bounds memory, not correctness.
KEEP_RUNS = 64

#: Concurrent background jobs across all runs. Each one fans its rows out over F896's pool.
JOB_WORKERS = 2

RowKey = tuple[str, tuple[str, ...]]


def row_key(row: dict) -> RowKey:
    """A row's identity for its verdict: the predicate reads the path AND the bases."""
    return str(row.get("path") or ""), tuple(str(b) for b in row.get("bases") or ())


class KeptVerdicts:
    """Per-run verdicts for network rows, refreshed by background jobs, one job per run at once."""

    def __init__(self, checks: FileChecks) -> None:
        self._checks = checks
        self._lock = threading.Lock()
        self._kept: OrderedDict[str, dict[RowKey, tuple[bool, float]]] = OrderedDict()
        self._busy: set[str] = set()
        self._ended: dict[str, float] = {}          # run → monotonic end of its last job
        self._closed = False
        self._jobs = ThreadPoolExecutor(max_workers=JOB_WORKERS,
                                        thread_name_prefix="rsched-files-net")

    def current(self, run_dir: Path, rows: Sequence[dict]) -> tuple[list[bool | None], bool]:
        """Each row's kept verdict, or None when there is no current one, and whether a refresh
        is due (a row unknown, or a verdict older than REFRESH_AFTER_S). Memory only.
        """
        now = monotonic()
        with self._lock:
            table = self._kept.get(str(run_dir))
            if table is not None:
                self._kept.move_to_end(str(run_dir))
        out: list[bool | None] = []
        due = False
        for row in rows:
            kept = table.get(row_key(row)) if table is not None else None
            age = now - kept[1] if kept is not None else None
            if kept is None or age is None or age > VERDICT_MAX_AGE_S:
                out.append(None)
                due = True
            else:
                out.append(kept[0])
                due = due or age > REFRESH_AFTER_S
        return out, due

    def checking(self, run_dir: Path) -> bool:
        """Whether a background job is checking this run's rows right now."""
        with self._lock:
            return str(run_dir) in self._busy

    def refresh(self, run_dir: Path, rows: Sequence[dict],
                roots: Callable[[], Sequence[Path]]) -> None:
        """Start a background check of `rows` unless one is already running for this run, or
        its last one ended under MIN_GAP_S ago. `roots` is called in the job, because resolving
        a root on a share is network I/O too.
        """
        key = str(run_dir)
        with self._lock:
            ended = self._ended.get(key)
            if key in self._busy or (ended is not None and monotonic() - ended < MIN_GAP_S):
                return
            self._busy.add(key)
        try:
            self._jobs.submit(self._run, run_dir, list(rows), roots)
        except RuntimeError:             # shut down: nothing will check, so nothing is busy
            with self._lock:
                self._busy.discard(key)

    def _run(self, run_dir: Path, rows: list[dict], roots: Callable[[], Sequence[Path]]) -> None:
        key = str(run_dir)
        try:
            full = tuple(roots())
            for start in range(0, len(rows), CHUNK):
                if self._closed:          # app shutdown: F896's pool takes no more work
                    break
                chunk = rows[start:start + CHUNK]
                try:
                    checked = self._checks.rows(chunk, run_dir, full)
                except Exception:
                    log.exception("files card: checking %d rows of %s raised", len(chunk), key)
                    self._forget(key, chunk)
                    continue
                self._keep(key, [(row_key(r), bool(r["servable"])) for r in checked])
        except Exception:
            log.exception("files card: resolving the roots of %s raised", key)
        finally:
            with self._lock:
                self._busy.discard(key)
                self._ended[key] = monotonic()
                while len(self._ended) > KEEP_RUNS:
                    self._ended.pop(next(iter(self._ended)))

    def _keep(self, key: str, verdicts: list[tuple[RowKey, bool]]) -> None:
        now = monotonic()
        with self._lock:
            table = self._kept.setdefault(key, {})
            self._kept.move_to_end(key)
            for rk, verdict in verdicts:
                table[rk] = (verdict, now)
            while len(self._kept) > KEEP_RUNS:
                self._kept.popitem(last=False)

    def _forget(self, key: str, rows: Sequence[dict]) -> None:
        """A chunk whose check raised told us nothing: its rows go back to unknown."""
        with self._lock:
            table = self._kept.get(key)
            if table is None:
                return
            for row in rows:
                table.pop(row_key(row), None)

    def shutdown(self) -> None:
        """Cancel queued jobs at app shutdown; a running one stops at its next chunk."""
        self._closed = True
        self._jobs.shutdown(wait=False, cancel_futures=True)


def card(rows: Sequence[dict], run_dir: Path, routine_dir: Path, server, checks: FileChecks,
         kept: KeptVerdicts) -> tuple[list[dict], bool]:
    """The files card's rows with `servable` set, in first-touched order, and whether a job is
    still checking rows that are unknown.

    An absolute path on a share is never touched here. Every other row is checked FRESH against
    the local roots (a local path cannot resolve into a root on a share), and a relative row that
    fails locally can still reach the share through a granted root that lives there, so its
    answer is the kept one — `None` until a job has checked it against every root.
    """
    mounts = netmounts.network_mounts()

    def on_share(p: Path) -> bool:
        return netmounts.on_network(p, mounts)

    paths = [str(row.get("path") or "") for row in rows]
    remote = [bool(p) and Path(p).is_absolute() and on_share(Path(p)) for p in paths]
    shared_root = any(on_share(root) for root in granted_roots(server, routine_dir))
    fresh = iter(checks.rows([row for row, far in zip(rows, remote, strict=True) if not far],
                             run_dir, roots_for_run(server, run_dir, routine_dir, skip=on_share)))
    checked: list[dict] = []
    later: list[int] = []
    for i, (row, far) in enumerate(zip(rows, remote, strict=True)):
        mine = {**row, "servable": None} if far else next(fresh)
        if far or (shared_root and not mine["servable"] and not Path(paths[i]).is_absolute()):
            later.append(i)
        checked.append(mine)
    if not later:
        return checked, False
    wanted = [rows[i] for i in later]
    verdicts, due = kept.current(run_dir, wanted)
    for i, verdict in zip(later, verdicts, strict=True):
        checked[i]["servable"] = verdict
    if due:
        kept.refresh(run_dir, wanted, lambda: roots_for_run(server, run_dir, routine_dir))
    return checked, kept.checking(run_dir) and None in verdicts
