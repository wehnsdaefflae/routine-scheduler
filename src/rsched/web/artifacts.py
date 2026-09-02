"""Shared artifact listing/serving — one implementation behind the routine AND the
conversation artifact panels (each router keeps only a thin handler on top).

The DELIVERABLE DIRS (R339/F336) are a documented convention, not a per-routine setting: a
run that commits a real deliverable must have a defined, working way to make it visible, and
`artifacts/` alone was not it — frame-fill-lab wrote a verified `reports/*.pdf` and the panel
stayed empty, with no mechanism anywhere to register the file. These three names are what
routines already use for exactly this, and they are deliverable-shaped by definition; a run
writing anywhere else is writing working state, not a deliverable. One list governs listing,
serving AND deletion, so a file that appears in the panel is always openable and removable.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

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

    The path-traversal guard this module's docstring calls load-bearing, in ONE place:
    delete and serve take the same client string, so a check hardened on one endpoint and
    not the other is a hole nobody sees. Containment is tested on the RESOLVED path — a
    lexical prefix test would wave 'artifacts/../routine.yaml' through. `verb` only
    completes the 400's wording; it never changes what is allowed.
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
    """
    p = _resolve_deliverable(base_dir, path, ARTIFACT_DIRS, "can be deleted")
    p.unlink()
    return {"ok": True, "deleted": str(p.relative_to(base_dir))}


def serve_file(base_dir: Path, path: str,
               subdirs: tuple[str, ...] = ARTIFACT_DIRS) -> FileResponse:
    """Serve one file raw (blob-rendered client-side) from the allowed subdirs ONLY. The
    conversation panel widens them to include `attachments/` — the one reason serving
    takes the dirs as an argument at all.
    """
    p = _resolve_deliverable(base_dir, path, subdirs, "are served")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, filename=p.name)
