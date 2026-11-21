"""Autocommit the routine's working directory at run end.

Routines never run git themselves, so the engine owns version control of their state/outputs.
A commit that does not land never blocks the run's finish. It is never silent either:
`libgit.commit` logs it and files a `commit_failed` health event carrying this run's id. The
files stay on disk, so the next run's autocommit takes them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..health_events import log_health_event
from ..libgit import commit, git
from ..paths import atomic_write_json, read_json

#: Where the last REPORTED size bucket of each oversize file lives — derived state beside the
#: other `.control` markers, never config.
SEEN_FILE = ".control/oversize-state-files.json"

#: A file at or above this size is left OUT of the routine's own repo, which is mirrored and
#: PUSHED (library-sync) — GitHub refuses any blob over 100 MB. Well under that on purpose. The
#: file itself stays on disk; only the commit skips it.
OVERSIZE_BYTES = 20 * 1024 * 1024


def oversize_files(routine_dir: Path, limit: int | None = None) -> list[tuple[str, int]]:
    """(repo-relative path, size) for every file the stage WOULD take — tracked plus
    untracked-not-ignored, git's own `add -A` view — at or over `limit`. Asking git keeps
    ignored trees (a live sshfs mount, a `.venv/`) out. Best-effort: an error lists nothing.
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


def _size_bucket(size: int) -> int:
    """The file's size in MB, rounded DOWN to a power of two — the identity of the event.

    An oversize file is oversize on every run; the bucket makes the event fire once, then only
    when the file has DOUBLED.
    """
    mb = max(1, size // (1024 * 1024))
    return 1 << (mb.bit_length() - 1)


def _newly_oversize(routines_home: Path, routine: str,
                    big: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """The subset worth an event — those whose size bucket is new for this routine — with
    the marker updated. Best-effort: an unreadable marker reports everything once more.
    """
    marker = Path(routines_home) / SEEN_FILE
    seen = read_json(marker)
    seen = seen if isinstance(seen, dict) else {}
    fresh = [(rel, size) for rel, size in big
             if seen.get(f"{routine}/{rel}") != _size_bucket(size)]
    if fresh:
        seen.update({f"{routine}/{rel}": _size_bucket(size) for rel, size in fresh})
        try:
            atomic_write_json(marker, seen)
        except OSError:
            pass
    return fresh


def autocommit(routine_dir: Path, message: str, *, routines_home: Path | None = None,
               run_id: str = "") -> None:
    """Commit the routine's working dir at run end, through the shared `libgit.commit` (its
    per-repo lock takes turns with a concurrent cross-routine writer; a commit that does not
    land is filed against `run_id` in `routines_home`'s health stream).

    Files over OVERSIZE_BYTES are excluded from the stage and each one is reported as an
    `oversize_state_file` health event (when `routines_home` is known) the first time it
    reaches a given size bucket, so the run's work is kept and the repo the instance pushes
    never carries a blob no remote will take.
    """
    if not (routine_dir / ".git").is_dir():
        return
    big = oversize_files(routine_dir)
    commit(routine_dir, message, routines_home=routines_home, run_id=run_id,
           exclude=[rel for rel, _size in big])
    if big and routines_home is not None:
        for rel, size in _newly_oversize(routines_home, routine_dir.name, big):
            log_health_event(routines_home, "oversize_state_file",
                             routine=routine_dir.name, run_id=run_id,
                             detail=f"{rel} is {size / (1024 * 1024):.0f} MB — left out of the "
                                    f"routine's own repo (ceiling {OVERSIZE_BYTES // (1024 * 1024)}"
                                    " MB); the file itself is untouched")
