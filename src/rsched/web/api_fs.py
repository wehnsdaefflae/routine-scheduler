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


def _denied_roots() -> list[Path]:
    """Credential stores the picker must not browse. The sandbox spends real machinery
    keeping these invisible to runs; the picker must not hand their layout (key file
    names, connection accounts, mount keys) to any bearer holder either. Names only is
    still reconnaissance.
    """
    from ..paths import config_file

    home = Path("~").expanduser()
    return [config_file().parent,      # secrets.env, connections.json, vapid keys, .mounts/
            home / ".credentials", home / ".ssh", home / ".claude"]


def _deny_sensitive(target: Path) -> None:
    for root in _denied_roots():
        try:
            root = root.resolve()
        except OSError:
            continue
        if target == root or root in target.parents:
            raise HTTPException(403, "that directory holds instance credentials — "
                                     "not browsable")


def _stat_entry(p: Path) -> tuple[bool, bool]:
    """(is_dir, unreadable) for ONE listing entry. A stat failure (permission-restricted
    or dead network mount, broken automount) must not silently demote the entry to an
    unclickable file row (F190): the entry stays a DIRECTORY, marked `unreadable`, so the
    picker shows it and descending yields an explicit error instead of a silent gap.
    """
    try:
        return p.is_dir(), False
    except OSError:
        return True, True


@router.get("/fs/list")
def list_dir(path: str = "") -> dict:
    """List the sub-entries of `path` (default: the daemon user's home). Directories first,
    then files, each case-insensitively sorted. Returns {path, parent, entries, truncated}
    where entries are {name, path, is_dir}; `parent` is null at the filesystem root.
    """
    try:
        # RuntimeError: an unknown `~user`, or a symlink loop (this interpreter's resolve());
        # ValueError: an embedded NUL. Each is a path the picker refuses, never a 500.
        target = Path(path.strip() or "~").expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(400, f"bad path: {exc}") from exc
    _deny_sensitive(target)
    # The listing itself says what is wrong, rather than a stat beforehand: `exists()` RAISES
    # on a dead mount (ENOTCONN, EIO), which made descending into exactly the entry F190 marks
    # `unreadable` a 500 instead of the explicit error that marking promises.
    try:
        children = [(c, *_stat_entry(c)) for c in target.iterdir()]
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no such directory: {target}") from exc
    except NotADirectoryError as exc:
        raise HTTPException(400, f"not a directory: {target}") from exc
    except PermissionError as exc:
        raise HTTPException(403, f"permission denied: {target}") from exc
    except OSError as exc:
        raise HTTPException(502, f"cannot list {target}: {exc.strerror or exc}") from exc
    children.sort(key=lambda t: (not t[1], t[0].name.lower()))
    entries = [{"name": c.name, "path": str(c), "is_dir": is_dir,
                **({"unreadable": True} if unreadable else {})}
               for c, is_dir, unreadable in children[:MAX_ENTRIES]]
    parent = str(target.parent) if target.parent != target else None
    return {"path": str(target), "parent": parent, "entries": entries,
            "truncated": len(children) > MAX_ENTRIES}
