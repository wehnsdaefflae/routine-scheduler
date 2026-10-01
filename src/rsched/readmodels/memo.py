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
recompute to keep a dozen stats. A RECOMPUTE is a use too: the stale entry leaves before the
fresh one enters, so a key whose sources moved neither keeps the eviction position it had
reached nor evicts a bystander to make room for itself.

The deep copies are made outside the module lock: a large value's copy never queues an
unrelated key's lookup behind it. Copying a cached value unlocked is safe because nothing
ever mutates one — each is a private copy (or, shared, immutable by contract).

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


_MISS = object()


def _take(key: str, fp: tuple) -> object:
    """The cached value for `key` while `fp` still vouches for it, moved to the youngest end
    (this is what makes the bound LRU) — else `_MISS`. The caller holds `_lock`.
    """
    hit = _cache.get(key)
    if hit is None or hit[0] != fp:
        return _MISS
    _cache[key] = _cache.pop(key)
    return hit[1]


def _store(key: str, fp: tuple, value: object) -> None:
    """Cache `value` at the youngest end, evicting the least recently used to stay in bound.
    The key's own stale entry leaves FIRST — assigning over it would keep the eviction position
    it had reached, and counting it would evict a bystander for room the key already holds.
    The caller holds `_lock`.
    """
    _cache.pop(key, None)
    while len(_cache) >= _MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (fp, value)


def _flight(key: str) -> threading.Lock:
    """The single-flight lock for `key`, created on its first miss. The caller holds `_lock`."""
    flight = _flights.get(key)
    if flight is None:
        if len(_flights) >= _MAX_ENTRIES:
            for stale in [k for k, f in _flights.items() if not f.locked()]:
                del _flights[stale]
        flight = _flights[key] = threading.Lock()
    return flight


def _memoized(key: str, paths: Sequence[Path], compute: Callable[[], T],  # noqa: UP047
              *, share: bool) -> T:
    fp = fingerprint(paths)
    with _lock:
        value = _take(key, fp)
        flight = _flight(key) if value is _MISS else None
    if flight is None:
        return value if share else copy.deepcopy(value)  # type: ignore[return-value]
    waited = not flight.acquire(blocking=False)
    if waited:
        flight.acquire()   # a leader is computing this key: take its result, not a second walk
    try:
        if waited:
            fp = fingerprint(paths)   # time passed in the queue — re-describe the sources now
            with _lock:
                value = _take(key, fp)
            if value is not _MISS:
                return value if share else copy.deepcopy(value)  # type: ignore[return-value]
        fresh = compute()
        kept = fresh if share else copy.deepcopy(fresh)
        with _lock:
            _store(key, fp, kept)
    finally:
        flight.release()
    return fresh


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


def run_tree(run_dir: Path) -> list[Path]:
    """`run_dir` plus every child run's dir beneath it, depth first in creation order. A child
    lives at `sub/<n>/` — `n` is the tree-wide counter, so numeric order is creation order and
    `10` comes after `2` — and its own children nest under ITS `sub/`. Only those numbered dirs
    are entered: a child's working files (artifacts, clones, spilled output) are never walked.
    """
    out = [run_dir]
    try:
        children = sorted((p for p in (run_dir / "sub").iterdir()
                           if p.name.isdigit() and p.is_dir()), key=lambda p: int(p.name))
    except OSError:
        return out
    for child in children:
        out += run_tree(child)
    return out


def transcript_paths(run_dir: Path) -> list[Path]:
    """Every transcript that feeds a run-scoped read-model: each level of the run tree's, gz
    variant included (retention swaps the raw file for .gz) and absent ones too, so a file
    appearing invalidates like one changing.
    """
    return [d / name for d in run_tree(run_dir)
            for name in ("transcript.jsonl", "transcript.jsonl.gz")]


def reset() -> None:
    """Drop everything (tests)."""
    with _lock:
        _cache.clear()
        _flights.clear()
