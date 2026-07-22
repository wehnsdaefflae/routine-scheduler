"""Stat-fingerprint memoization — the read-model caching discipline.

A derived view is recomputed only when any of its input files changed, judged by the
same fingerprint the registry uses (inode + mtime_ns + size — atomic tmp+rename rewrites
always change the inode, appends change size/mtime). Values are returned as DEEP COPIES
so a cached result can never be mutated by one consumer under another's feet; the cache
is process-local and bounded (oldest-inserted evicted) — a pure cache, deletable state.
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
            return hit[1] if share else copy.deepcopy(hit[1])  # type: ignore[return-value]
    value = compute()
    with _lock:
        while len(_cache) >= _MAX_ENTRIES:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (fp, value if share else copy.deepcopy(value))
    return value


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
