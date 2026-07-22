"""Server-side directory browser powering the routine page's filesystem-root PICKER.

The fs-root fields are server paths (the daemon reads/writes them), so a real picker has to
list the *daemon's* filesystem, not the browser's. This lists a directory's entries — names
and is-dir only, never file contents — so the operator can navigate and pick an actual path
instead of typing one. Bearer-authed like every other /api route; the scope is exactly what
the fs-root text fields always accepted (any path the daemon user can reach), now browsable.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["fs"])

MAX_ENTRIES = 2000  # cap a pathologically large directory; the picker notes truncation


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:      # broken symlink, unreadable — treat as a non-directory leaf
        return False


@router.get("/fs/list")
def list_dir(path: str = "") -> dict:
    """List the sub-entries of `path` (default: the daemon user's home). Directories first,
    then files, each case-insensitively sorted. Returns {path, parent, entries, truncated}
    where entries are {name, path, is_dir}; `parent` is null at the filesystem root.
    """
    try:
        target = Path(path.strip() or "~").expanduser()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, f"bad path: {exc}") from exc
    try:
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(400, f"bad path: {exc}") from exc
    if not target.exists():
        raise HTTPException(404, f"no such directory: {target}")
    if not _is_dir(target):
        raise HTTPException(400, f"not a directory: {target}")
    try:
        children = [(c, _is_dir(c)) for c in target.iterdir()]
    except PermissionError as exc:
        raise HTTPException(403, f"permission denied: {target}") from exc
    children.sort(key=lambda t: (not t[1], t[0].name.lower()))
    entries = [{"name": c.name, "path": str(c), "is_dir": is_dir}
               for c, is_dir in children[:MAX_ENTRIES]]
    parent = str(target.parent) if target.parent != target else None
    return {"path": str(target), "parent": parent, "entries": entries,
            "truncated": len(children) > MAX_ENTRIES}
