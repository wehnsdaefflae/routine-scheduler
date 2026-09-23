"""Stat-fingerprint memoization — the read-model caching discipline.

A derived view is recomputed only when any of its input files changed, judged by the
same fingerprint the registry uses (inode + mtime_ns + size — atomic tmp+rename rewrites
always change the inode, appends change size/mtime). Values are returned as DEEP COPIES
so a cached result can never be mutated by one consumer under another's feet; the cache
is process-local and bounded (LEAST-RECENTLY-USED evicted) — a pure cache, deletable state.

Least-recently-USED, not oldest-inserted, and the difference is the whole point of the
bound: a Python dict does not reorder on re-assignment, so an entry's eviction position
would be fixed at its first insert. The expensive keys are the ones fetched EARLY — the
library lint and the util catalog land on the first routine-page open of a boot — while the
cheap per-dir keys (`decisions:runs:` ×143, `recipe-log:` ×35, `recipe-baseline:` ×35 per
day) arrive later and in bulk. Evicting by insert order would therefore throw out a 4-second
recompute to keep a dozen stats.

Misses are SINGLE-FLIGHT per key: the first caller computes while every concurrent caller
for the same key waits on it, then re-reads the fresh entry. A burst of identical requests
(every open console tab refetching `/api/questions` on one bus event — 10-20 in flight on
2026-09-14, each a 265 ms catalog walk under the GIL, each starving the others) therefore
costs ONE compute. A waiter re-stats after the wait: the fingerprint that vouches for a
value must describe the sources as they were before THAT value's compute, never before
the queue.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

# a classic TypeVar, not PEP 695 generics: pdoc (the Help tab's API docs) cannot parse
# `def f[T](…)` yet and warns on every docs build
T = TypeVar("T")

_MAX_ENTRIES = 512

_lock = threading.Lock()
_cache: dict[str, tuple[tuple, object]] = {}
# one in-progress compute per key (see the module docstring); keys come and go with run dirs,
# so the table is swept of unheld locks once it reaches the cache's own bound
_flights: dict[str, threading.Lock] = {}


def fingerprint(paths: Sequence[Path]) -> tuple:
    """The inputs' identity: (path, inode, mtime_ns, size) per file, a missing marker for
    absent ones — so a file appearing, vanishing, growing, or being atomically replaced
    all invalidate.
    """
    out: list[tuple] = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None))
    return tuple(out)


def memoized(key: str, paths: Sequence[Path], compute: Callable[[], T]) -> T:  # noqa: UP047 — pdoc can't parse PEP 695 generics
    """`compute()`'s result for `key`, reused while every path in `paths` is unchanged."""
    return _memoized(key, paths, compute, share=False)


def memoized_shared(key: str, paths: Sequence[Path],  # noqa: UP047 — see memoized
                    compute: Callable[[], T]) -> T:
    """Like `memoized`, but the cached value itself is returned (no deep copy) — for big
    flat record lists where copying per request would cost what the memo saves. The
    caller's contract: treat the result as IMMUTABLE.
    """
    return _memoized(key, paths, compute, share=True)


def _memoized(key: str, paths: Sequence[Path], compute: Callable[[], T],  # noqa: UP047
              *, share: bool) -> T:
    fp = fingerprint(paths)
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == fp:
            _cache[key] = _cache.pop(key)   # youngest end: this is what makes the bound LRU
            return hit[1] if share else copy.deepcopy(hit[1])  # type: ignore[return-value]
        flight = _flights.get(key)
        if flight is None:
            if len(_flights) >= _MAX_ENTRIES:
                for stale in [k for k, f in _flights.items() if not f.locked()]:
                    del _flights[stale]
            flight = _flights[key] = threading.Lock()
    waited = not flight.acquire(blocking=False)
    if waited:
        flight.acquire()   # a leader is computing this key: take its result, not a second walk
    try:
        if waited:
            fp = fingerprint(paths)   # time passed in the queue — re-describe the sources now
            with _lock:
                hit = _cache.get(key)
                if hit is not None and hit[0] == fp:
                    _cache[key] = _cache.pop(key)
                    return hit[1] if share else copy.deepcopy(hit[1])  # type: ignore[return-value]
        value = compute()
        with _lock:
            while len(_cache) >= _MAX_ENTRIES:
                _cache.pop(next(iter(_cache)))
            _cache[key] = (fp, value if share else copy.deepcopy(value))
    finally:
        flight.release()
    return value


def tree_paths(root: Path, *patterns: str) -> list[Path]:
    """`root` itself plus every file under it matching `patterns` — the fingerprint list
    for a view derived from a whole DIRECTORY (a library's docs, a util tree). The root is
    included so a file appearing or vanishing invalidates even when nothing that survives
    changed; the stats cost a few hundred microseconds against parses that cost seconds.
    """
    out = [root]
    for pattern in patterns:
        out.extend(sorted(root.glob(pattern)))
    return out


def transcript_paths(run_dir: Path) -> list[Path]:
    """Every transcript that feeds a run-scoped read-model: the run's own plus the whole
    child tree's, gz variants included (retention swaps the raw file for .gz).
    """
    return [run_dir / "transcript.jsonl", run_dir / "transcript.jsonl.gz",
            *sorted(run_dir.glob("sub/**/transcript.jsonl")),
            *sorted(run_dir.glob("sub/**/transcript.jsonl.gz"))]


def reset() -> None:
    """Drop everything (tests)."""
    with _lock:
        _cache.clear()
        _flights.clear()
