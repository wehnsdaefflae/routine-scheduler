"""The recommender's FOLDER GRANTS — what it tells the model about roots, and the one mechanical
correction it makes to what comes back.

A routine's own directory is already readable and writable by every run (`ownseal` mounts it,
minus the sealed engine stores), so a root naming it, or a path inside it, grants nothing — and
a RELATIVE root can only mean one, since a run resolves relative paths against that directory.
feeld-webui-maintainer's creation draft (2026-11-20) proposed `fs_write_roots: ["work"]`: the
prompt never said where the routine lives or that roots are absolute, the judge never asked, and
the accept refused the whole draft over it.

The prompt now says both (`prompt_line`) — the cause. `without_own_dir` is the check no prompt
can guarantee: it drops such entries from a proposed list before the judge sees it, so a model
that still writes one costs the proposal that entry rather than the whole field. A root the
routine ALREADY holds is kept as it is: removing the operator's own value is a change nobody
asked the recommender to propose.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..paths import expand

ROOT_KEYS = ("fs_read_roots", "fs_write_roots")


def own_dir(server, slug: str) -> Path:
    """The routine's own directory, `~` expanded — what every run already reaches."""
    return Path(os.path.normpath(expand(str(Path(server.routines_home) / slug))))


def prompt_line(own: Path) -> str:
    """The sentence the recommender's prompt carries about folder grants."""
    return (
        "fs_read_roots and fs_write_roots are lists of ABSOLUTE paths (/abs/path or ~/path). "
        f"This routine's own directory is {own}: every run already reads and writes everything "
        "inside it (its working files, state/, scripts/), so never propose that directory, a "
        "path inside it, or a relative path — a root names a place OUTSIDE it the task needs, "
        "such as a repository it maintains.")


def without_own_dir(value: object, own: Path, held: object) -> object:
    """`value` with every root that is `own`, inside it, or relative to it removed — except a
    root the routine already holds (`held`). Anything not a list is returned as it came, for
    the judge to refuse.
    """
    if not isinstance(value, list):
        return value
    kept = set(held) if isinstance(held, list) else set()
    return [root for root in value
            if root in kept or not (isinstance(root, str) and _inside(root, own))]


def _inside(root: str, own: Path) -> bool:
    """Is `root` the routine's own directory or a path under it, a relative root resolved
    against that directory (where a run resolves it)? `../elsewhere` is not — the judge then
    refuses it as relative, which is the honest answer for a path that names no fixed place.
    """
    text = root.strip().rstrip("/") or "/"
    try:
        path = expand(text)
    except RuntimeError:
        return False
    if not path.is_absolute():
        path = own / path
    path = Path(os.path.normpath(path))
    return path == own or own in path.parents
