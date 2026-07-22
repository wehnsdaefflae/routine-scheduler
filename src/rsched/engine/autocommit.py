"""Autocommit the routine's working directory at run end (best-effort).

Routines have no shell, so the engine owns version control of their state/outputs.
This is a best-effort operation: failures are silently ignored so they never block
the run's finish.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..paths import file_lock, repo_lock_path


def autocommit(routine_dir: Path, message: str) -> None:
    """Commit the routine's working dir at run end with the neutral identity (best-effort).
    Routines have no shell, so the engine owns version control of their state/outputs.

    Held under the per-repo commit lock (`paths.repo_lock_path`) so a cross-routine writer
    committing this same dir concurrently — the routine-improver's `git-sync` of a target
    that is mid-run — takes turns with this autocommit instead of colliding on `index.lock`.
    The `git-sync` util flocks the same file.
    """
    if not (routine_dir / ".git").is_dir():
        return
    try:
        with file_lock(repo_lock_path(routine_dir)):
            subprocess.run(["git", "-C", str(routine_dir), "add", "-A"],
                           capture_output=True, timeout=30, check=False)
            subprocess.run(["git", "-C", str(routine_dir),
                            "-c", "user.name=routine-scheduler",
                            "-c", "user.email=noreply@routine-scheduler.local",
                            "commit", "-qm", message],
                           capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
