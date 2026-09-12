"""Autocommit the routine's working directory at run end (best-effort).

Routines never run git themselves, so the engine owns version control of their state/outputs.
This is a best-effort operation: failures are silently ignored so they never block
the run's finish.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..health_events import log_health_event
from ..libgit import commit, git

#: A file at or above this size is left OUT of the routine's own repo. The routine repo is
#: mirrored into the library repo and PUSHED (library-sync), and GitHub refuses a push
#: carrying any blob over 100 MB — one 223 MB NAS inventory a run wrote to state/ blocked
#: every library push for days and the mirror's history had to be rewritten to drop it.
#: Well under that limit on purpose: a mirror carries files for people to read, and nothing
#: a person reads is 20 MB. The file itself stays on disk, untouched; only the commit skips it.
OVERSIZE_BYTES = 20 * 1024 * 1024


def oversize_files(routine_dir: Path, limit: int | None = None) -> list[tuple[str, int]]:
    """(repo-relative path, size) for every file the stage WOULD take — tracked plus
    untracked-not-ignored, exactly git's own `add -A` view — at or over `limit`. Asking git
    rather than walking the tree keeps ignored trees out of it: `mnt/` may be a live sshfs
    share, `.venv/` is thousands of files, and neither would ever be committed anyway.
    Best-effort: no repo or a git error lists nothing.
    """
    found: list[tuple[str, int]] = []
    if limit is None:
        limit = OVERSIZE_BYTES          # read at call time, so a test can lower the ceiling
    try:
        listed = git(routine_dir, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except (OSError, subprocess.TimeoutExpired):
        return found
    if listed.returncode != 0:
        return found
    for rel in listed.stdout.split("\0"):
        if not rel:
            continue
        path = routine_dir / rel
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size >= limit:
                found.append((rel, path.stat().st_size))
        except OSError:
            continue
    return sorted(found)


def autocommit(routine_dir: Path, message: str, *, routines_home: Path | None = None,
               run_id: str = "") -> None:
    """Commit the routine's working dir at run end (best-effort), through the shared
    `libgit.commit` (F285/F318 — this module once re-implemented it verbatim): its
    per-repo lock means a cross-routine writer committing this same dir concurrently
    — the routine-improver's `git-sync` of a target that is mid-run — takes turns with
    this autocommit instead of colliding on `index.lock` (the `git-sync` util flocks the
    same file), and its identity flags keep the neutral author even in a routine repo
    that never persisted git config.

    Files over OVERSIZE_BYTES are excluded from the stage and each one is reported as an
    `oversize_state_file` health event (when `routines_home` is known), so the run's work
    is kept and the repo the instance pushes never carries a blob no remote will take.
    """
    if not (routine_dir / ".git").is_dir():
        return
    big = oversize_files(routine_dir)
    commit(routine_dir, message, exclude=[rel for rel, _size in big])
    if big and routines_home is not None:
        for rel, size in big:
            log_health_event(routines_home, "oversize_state_file",
                             routine=routine_dir.name, run_id=run_id,
                             detail=f"{rel} is {size / (1024 * 1024):.0f} MB — left out of the "
                                    f"routine's own repo (ceiling {OVERSIZE_BYTES // (1024 * 1024)}"
                                    " MB); the file itself is untouched")
