"""Autocommit the routine's working directory at run end (best-effort).

Routines have no shell, so the engine owns version control of their state/outputs.
This is a best-effort operation: failures are silently ignored so they never block
the run's finish.
"""

from __future__ import annotations

from pathlib import Path

from ..libgit import commit


def autocommit(routine_dir: Path, message: str) -> None:
    """Commit the routine's working dir at run end (best-effort), through the shared
    `libgit.commit` (F285/F318 — this module once re-implemented it verbatim): its
    per-repo lock means a cross-routine writer committing this same dir concurrently
    — the routine-improver's `git-sync` of a target that is mid-run — takes turns with
    this autocommit instead of colliding on `index.lock` (the `git-sync` util flocks the
    same file), and its identity flags keep the neutral author even in a routine repo
    that never persisted git config.
    """
    if not (routine_dir / ".git").is_dir():
        return
    commit(routine_dir, message)
