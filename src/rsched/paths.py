"""Canonical path helpers and atomic file IO.

Every cross-process file (status.json, inbox messages, answers, control.json) goes through
atomic_write so a reader never sees a partial file.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path


def expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser()


def config_file() -> Path:
    env = os.environ.get("RSCHED_CONFIG")
    if env:
        return expand(env)
    return expand("~/.config/routine-scheduler/config.yaml")


def atomic_write(path: str | Path, data: str | bytes, *, mode: int | None = None) -> Path:
    """Write via tmp file + rename in the target directory (same filesystem). A concurrent
    reader sees the old file or the new one, never a partial write. `mode` (permission bits,
    e.g. from a prior `stat().st_mode`) is applied to the new file before the rename — pass it
    when overwriting so the temp file's default 0600 doesn't drop an existing file's bits
    (notably +x); omit it for new files.

    Deliberately NO fsync (file or directory): the guarantee is concurrent-reader
    atomicity, not power-loss durability — every consumer here is a cache, telemetry, or
    state that a crashed box legitimately rebuilds/re-derives.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmode, encoding = ("wb", None) if isinstance(data, bytes) else ("w", "utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, fmode, encoding=encoding) as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path: str | Path, obj: object) -> Path:
    return atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | Path, default: object = None) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def within(root: Path, candidate: Path) -> bool:
    """True if candidate (resolved) lies inside root (resolved)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_rel(base: Path, rel: str, extra_roots: Sequence[Path] = ()) -> Path:
    """Resolve a path from an action: relative → under base; absolute → must fall inside
    base or one of extra_roots. Raises PermissionError otherwise.
    """
    p = expand(rel)
    candidate = p if p.is_absolute() else (base / p)
    roots = [base, *extra_roots]
    for root in roots:
        if within(root, candidate):
            return candidate.resolve()
    allowed = ", ".join(str(r) for r in roots)
    raise PermissionError(f"path {rel!r} is outside the allowed roots ({allowed})")


def repo_lock_path(home: Path) -> Path:
    """The per-repo commit-lock file for the git repo containing `home`, placed inside the
    shared `.git` dir so every writer of the SAME repo — no matter which subdir it passes as
    `home` — agrees on one lock. Falls back to a dotfile at the tree root for the (never, for
    the library) case of a worktree/submodule `.git` file or no repo at all.
    """
    cur = Path(home).resolve()
    for d in (cur, *cur.parents):
        g = d / ".git"
        if g.is_dir():
            return g / "rsched-commit.lock"
        if g.is_file():
            return d / ".rsched-commit.lock"
    return cur / ".rsched-commit.lock"


@contextmanager
def file_lock(lock_path: str | Path, *, timeout: float = 30.0,
              poll: float = 0.05) -> Iterator[bool]:
    """Advisory exclusive lock across processes via `fcntl.flock`. Yields True once held,
    or False if `timeout` elapsed without acquiring — in which case the caller proceeds
    BEST-EFFORT (a stale/hung holder must never deadlock a commit; git's own 30s subprocess
    timeout bounds the wait regardless). The lock file itself is never written or committed.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= end:
                    break
                time.sleep(poll)
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
