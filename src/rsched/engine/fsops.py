"""The native filesystem actions (D120=A): delete / move / mkdir.

Same jail and same seals as write_file — the parent resolved against the write roots, and
`fileops._write_gate` for runs/, .util_outputs/, .doc_cache/, .memory/, routine.yaml, the
finish line and the recipe — plus two rules of their own. A delete or a move acts on the ENTRY
it names, as `rm` and `mv` do (`_entry`): a link is removed or relocated, never what it points
to. And the
destructive-op grounding rule: removing or relocating a path OUTSIDE the routine's own dir
requires having seen it this run — the reasoning of write_file's overwrite gate, extended to
destruction (`_unseen_destruction`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..paths import expand
from .fileops import _own_dir, _write_gate
from .run_context import RunContext, resolve_action_path


def _entry(ctx: RunContext, raw: str) -> Path:
    """The path an action NAMES, its final component left unresolved — the directory entry
    itself, as `rm` and `mv` see it. delete and move act on entries: resolving the whole path
    followed a symbolic link, so deleting a link deleted what it pointed to and moving one
    moved its target away, each leaving the link dangling (the `is_symlink` branches below were
    written for links and could never run). The parent is resolved and jailed like any path.
    """
    p = expand(raw)
    if p.name in ("", ".", ".."):
        return resolve_action_path(ctx, raw, write=True)
    return resolve_action_path(ctx, str(p.parent), write=True) / p.name


def _tree_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _unseen_destruction(ctx: RunContext, resolved, what: str) -> str | None:
    """The grounding gate for delete and move-src: destroying a path outside the routine's
    own dir that this run has never read. The own dir is exempt (state cleanup is a
    routine's normal mode); elsewhere the model must have LOOKED at what it destroys — a
    read_file of the path, which for a directory is its listing, for a PDF or Office document
    its converted text (docread.py), and for any other binary or oversized file its size (the
    refusal grounds too). A shell `ls` does not count: the
    engine cannot see what a shell command showed, only what read_file returned. The gate
    text names the two forms because a run that reads "read_file it first" about a season
    pack once read_file'd a 1.5 GB .mkv to comply (2026-09-14). A symbolic link is exempt
    too: removing or relocating one destroys nothing but the link.
    """
    if (resolved.is_symlink() or resolved.is_relative_to(_own_dir(ctx))
            or str(resolved) in ctx.seen_paths):
        return None
    return (f"this {what} a path outside the routine's own dir that this run has never "
            "read — read_file it first (a directory reads as its listing, a binary or "
            "oversized file as its size: both count), then remove it knowingly, so a stray "
            "call cannot destroy something sight-unseen")


def do_delete(action: dict, ctx: RunContext) -> dict:
    try:
        path = _entry(ctx, action["path"])
        if err := _write_gate(ctx, path, creates=False):
            return {"kind": "delete", "path": action["path"], "error": err}
        if not path.exists() and not path.is_symlink():
            return {"kind": "delete", "path": action["path"],
                    "error": "no such path — read_file its parent directory (a directory "
                             "reads as its listing) to see what is actually there"}
        if len(path.parts) == 1:                     # the filesystem root is its only part
            return {"kind": "delete", "path": action["path"],
                    "error": "refusing to delete a filesystem-root path"}
        if err := _unseen_destruction(ctx, path, "deletes"):
            return {"kind": "delete", "path": action["path"], "error": err}
        if path.is_dir() and not path.is_symlink():
            if not action.get("recursive"):
                return {"kind": "delete", "path": action["path"],
                        "error": "path is a directory — pass recursive: true to remove the "
                                 "whole tree (a stray call must not be able to wipe one)"}
            freed = _tree_size(path)
            what = "dir"
            shutil.rmtree(path)
        else:
            freed = path.lstat().st_size              # a link's own size, never its target's
            what = "link" if path.is_symlink() else "file"
            path.unlink()
    except (OSError, PermissionError) as exc:
        return {"kind": "delete", "path": action["path"], "error": str(exc)}
    return {"kind": "delete", "path": action["path"], "type": what,
            "bytes_freed": freed, "removed": True}


def do_move(action: dict, ctx: RunContext) -> dict:
    try:
        src = _entry(ctx, action["src"])
        dst = _entry(ctx, action["dst"])
        for p, field in ((src, "src"), (dst, "dst")):
            if err := _write_gate(ctx, p, creates=field == "dst"):
                return {"kind": "move", "src": action["src"], "dst": action["dst"],
                        "error": f"{field}: {err}"}
        if not src.exists() and not src.is_symlink():
            return {"kind": "move", "src": action["src"], "dst": action["dst"],
                    "error": "no such source path — read_file its parent directory (a "
                             "directory reads as its listing) to see what is actually there"}
        if dst.exists() or dst.is_symlink():
            return {"kind": "move", "src": action["src"], "dst": action["dst"],
                    "error": "destination already exists — move refuses to overwrite; "
                             "delete it first if the replacement is really wanted"}
        if err := _unseen_destruction(ctx, src, "moves"):
            return {"kind": "move", "src": action["src"], "dst": action["dst"], "error": err}
        moved = _tree_size(src) if src.is_dir() and not src.is_symlink() \
            else src.lstat().st_size
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except (OSError, PermissionError) as exc:
        return {"kind": "move", "src": action["src"], "dst": action["dst"], "error": str(exc)}
    return {"kind": "move", "src": action["src"], "dst": action["dst"],
            "bytes_moved": moved, "moved": True}


def do_mkdir(action: dict, ctx: RunContext) -> dict:
    try:
        path = resolve_action_path(ctx, action["path"], write=True)
        if err := _write_gate(ctx, path):
            return {"kind": "mkdir", "path": action["path"], "error": err}
        existed = path.is_dir()
        if existed and not action.get("parents"):
            return {"kind": "mkdir", "path": action["path"],
                    "error": "path already exists — pass parents: true to treat that as "
                             "success"}
        if action.get("parents"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir()
    except OSError as exc:                       # FileExistsError and PermissionError included
        return {"kind": "mkdir", "path": action["path"], "error": str(exc)}
    return {"kind": "mkdir", "path": action["path"], "created": not existed}
