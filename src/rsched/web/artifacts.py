"""Shared artifact listing/serving — one implementation behind the routine AND the
conversation artifact panels (each router keeps only a thin handler on top) — and the
race-free open every web read of a file under a run home goes through.

The DELIVERABLE DIRS (R339/F336) are a documented convention, not a per-routine setting: a
run that commits a real deliverable must have a defined, working way to make it visible, and
`artifacts/` alone was not it — frame-fill-lab wrote a verified `reports/*.pdf` and the panel
stayed empty, with no mechanism anywhere to register the file. These three names are what
routines already use for exactly this, and they are deliverable-shaped by definition; a run
writing anywhere else is writing working state, not a deliverable. One list governs listing,
serving AND deletion, so a file that appears in the panel is always openable and removable.

CONTAINMENT IS PROVEN ON WHAT WAS OPENED (`open_within`). Every file served here sits in a
directory a jailed util may write — and that util may hold the routine token, which reads
these routes. Checked on a path BEFORE the open, containment can be raced: the util lets the
check pass on a real directory, swaps one component for a symlink before the open, and the
console reads for it a file its own jail forbids (the daemon's config, the operator's token in
it). So the check is made on the kernel's own name for the opened descriptor.
"""

from __future__ import annotations

import mimetypes
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.types import Receive, Scope, Send

from ..paths import resolve_rel, within

#: Where a run's deliverables live. `artifacts/` is the name to reach for in new work; the
#: other two are long-standing conventions in live routines (reports/ for rendered documents,
#: output/ for generated pages/feeds) that the panel used to be blind to.
ARTIFACT_DIRS = ("artifacts", "reports", "output")

#: Path segments that hold INTERMEDIATES, never deliverables. A rendering pipeline builds in
#: `<dir>/build/` and copies the finished file up — frame-fill-lab's audit run left 76 page
#: PNGs there beside 27 real deliverables, which is a panel nobody can read. Dot-segments go
#: too (caches, VCS internals). A deliverable is the thing you would hand someone.
SKIP_SEGMENTS = frozenset({"build", "__pycache__", "node_modules"})

#: Every served file is NEVER CACHED (R1682). A deliverable is a MUTABLE file under a STABLE
#: name: the action documentation tells every run that re-writing a filename updates that
#: artifact in place, so the same URL is expected to return different bytes over a run's life.
#: Served without these, a rewritten file could still be read from the client's cache — the
#: reported failure was a decision surface (`asks-2026-09-19.html`, 7,886 → 13,371 bytes)
#: whose two new decision cards the user never saw, while the run recorded them delivered and
#: a grep of the file on disk confirmed them present. Nothing a routine can check
#: distinguishes "written" from "visible", so the guarantee has to be made where it is served.
NO_STORE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


def _open_beneath(path: Path, roots: Sequence[Path], flags: int) -> int:
    """`os.open(path, flags)`, kept only when the kernel's name for the opened descriptor lies
    under one of `roots` (module docstring); PermissionError otherwise, nothing left open.
    """
    fd = os.open(path, flags | os.O_CLOEXEC)
    try:
        opened = Path(f"/proc/self/fd/{fd}").readlink()
        if not any(opened.is_relative_to(root.resolve()) for root in roots):
            raise PermissionError(f"{path} resolves outside "
                                  f"{', '.join(str(r) for r in roots)}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_within(path: Path, roots: Sequence[Path]) -> int:
    """A read-only descriptor for the REGULAR file at `path`, proven to lie under one of
    `roots` after it was opened. Non-blocking, so a FIFO swapped in by a util is refused
    rather than holding a worker forever. The caller owns the descriptor.
    """
    fd = _open_beneath(path, roots, os.O_RDONLY | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PermissionError(f"{path} is not a regular file")
    return fd


class _OpenedFileResponse(FileResponse):
    """A FileResponse over a descriptor `open_within` has already vetted.

    It streams `/proc/self/fd/<fd>`, which the kernel resolves to the opened file itself and
    never back through the path a util can rearrange, and closes the descriptor once the
    response is over — sent, HEAD-only, or cut off by a client that went away.
    """

    def __init__(self, fd: int, *, filename: str, media_type: str) -> None:
        super().__init__(f"/proc/self/fd/{fd}", filename=filename, media_type=media_type,
                         headers=NO_STORE, stat_result=os.fstat(fd))
        self._fd = fd

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            os.close(self._fd)


def file_response(fd: int, name: str, *, default_media: str) -> FileResponse:
    """Serve the file behind `fd` (from `open_within`) raw and never cached (`NO_STORE`), its
    type guessed from `name`. Takes ownership of the descriptor.
    """
    media = mimetypes.guess_type(name)[0] or default_media
    return _OpenedFileResponse(fd, filename=name, media_type=media)


def _is_deliverable(rel: Path) -> bool:
    """False for anything under an intermediates directory (or a dot-dir)."""
    return not any(part in SKIP_SEGMENTS or part.startswith(".") for part in rel.parts[1:])


def list_artifacts(base_dir: Path) -> list[dict]:
    """Everything under the deliverable dirs — newest first, each row's `path` relative to
    the routine dir (so the panel's open/delete calls address it unambiguously).
    """
    out: list[dict] = []
    for sub in ARTIFACT_DIRS:
        art = base_dir / sub
        if not art.is_dir():
            continue
        for p in art.rglob("*"):
            if p.is_file():
                rel = p.relative_to(base_dir)
                if not _is_deliverable(rel):
                    continue
                st = p.stat()
                out.append({"path": str(rel), "name": p.name,
                            "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _resolve_deliverable(base_dir: Path, path: str, subdirs: tuple[str, ...],
                         verb: str) -> Path:
    """Resolve a client-supplied path to one real file under `subdirs`, or raise.

    The path-traversal guard, in ONE place: delete and serve take the same client string, so
    a check hardened on one endpoint and not the other is a hole nobody sees. Containment is
    tested on the RESOLVED path — a lexical prefix test would wave
    'artifacts/../routine.yaml' through — and proven again on what is opened (module
    docstring), since a check made before the open can be raced. `verb` only completes the
    400's wording; it never changes what is allowed.
    """
    try:
        p = resolve_rel(base_dir, path.lstrip("/"))
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not any(within(base_dir / sub, p) for sub in subdirs):
        allowed = " and ".join(f"{s}/" for s in subdirs)
        raise HTTPException(400, f"only {allowed} files {verb}")
    if not p.is_file():
        raise HTTPException(404, f"no file {path!r}")
    return p


def delete_artifact(base_dir: Path, path: str) -> dict:
    """Delete ONE artifact file — the sidebar's user-facing remove (2026-08-14 order:
    artifacts must be deletable from the web UI). Deletion is scoped to ARTIFACT_DIRS and
    nothing wider — a conversation's `attachments/` are the USER'S uploads, servable but
    never removable by a panel click — which is why this takes no subdirs argument.

    The unlink is made relative to the file's directory as OPENED and proven contained, for
    the module docstring's reason: through a path, a component swapped for a symlink after
    the check would turn the operator's click into a delete of a same-named file wherever
    the util pointed it.
    """
    p = _resolve_deliverable(base_dir, path, ARTIFACT_DIRS, "can be deleted")
    changed = f"{path!r} changed while it was being deleted"
    try:
        dfd = _open_beneath(p.parent, [base_dir / sub for sub in ARTIFACT_DIRS],
                            os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise HTTPException(400, f"{changed}: {exc}") from exc
    try:
        os.unlink(p.name, dir_fd=dfd)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no file {path!r}") from exc
    except OSError as exc:
        raise HTTPException(400, f"{changed}: {exc}") from exc
    finally:
        os.close(dfd)
    return {"ok": True, "deleted": str(p.relative_to(base_dir))}


def serve_file(base_dir: Path, path: str,
               subdirs: tuple[str, ...] = ARTIFACT_DIRS) -> FileResponse:
    """Serve one file raw (blob-rendered client-side) from the allowed subdirs ONLY, never
    cached (`NO_STORE`). The conversation panel widens them to include `attachments/` — the
    one reason serving takes the dirs as an argument at all.
    """
    p = _resolve_deliverable(base_dir, path, subdirs, "are served")
    try:
        fd = open_within(p, [base_dir / sub for sub in subdirs])
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no file {path!r}") from exc
    except OSError as exc:
        raise HTTPException(400, f"{path!r} changed while it was being served: {exc}") from exc
    return file_response(fd, p.name, default_media="application/octet-stream")
