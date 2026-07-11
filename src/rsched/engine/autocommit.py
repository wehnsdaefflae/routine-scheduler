"""Autocommit the routine's working directory at run end (best-effort).

Routines have no shell, so the engine owns version control of their state/outputs.
This is a best-effort operation: failures are silently ignored so they never block
the run's finish.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def autocommit(routine_dir: Path, message: str) -> None:
    """Commit the routine's working dir at run end with the neutral identity (best-effort).
    Routines have no shell, so the engine owns version control of their state/outputs."""
    if not (routine_dir / ".git").is_dir():
        return
    try:
        subprocess.run(["git", "-C", str(routine_dir), "add", "-A"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(routine_dir),
                        "-c", "user.name=routine-scheduler",
                        "-c", "user.email=noreply@routine-scheduler.local",
                        "commit", "-qm", message], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
