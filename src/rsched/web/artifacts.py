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
                st = p.stat()
                out.append({"path": str(p.relative_to(base_dir)), "name": p.name,
                            "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def delete_artifact(base_dir: Path, path: str,
                    subdirs: tuple[str, ...] = ARTIFACT_DIRS) -> dict:
    """Delete ONE artifact file — the sidebar's user-facing remove (2026-08-14 order:
    artifacts must be deletable from the web UI). Same resolved-path containment as
    serve_file: only files under the allowed subdirs are deletable, so
    'artifacts/../routine.yaml' can never pass.
    """
    try:
        p = resolve_rel(base_dir, path.lstrip("/"))
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not any(within(base_dir / sub, p) for sub in subdirs):
        allowed = " and ".join(f"{s}/" for s in subdirs)
        raise HTTPException(400, f"only {allowed} files can be deleted")
    if not p.is_file():
        raise HTTPException(404, f"no file {path!r}")
    p.unlink()
    return {"ok": True, "deleted": str(p.relative_to(base_dir))}


def serve_file(base_dir: Path, path: str,
               subdirs: tuple[str, ...] = ARTIFACT_DIRS) -> FileResponse:
    """Serve one file raw (blob-rendered client-side) from the allowed subdirs ONLY.
    The containment check runs on the RESOLVED path — 'artifacts/../routine.yaml' must
    not pass.
    """
    try:
        p = resolve_rel(base_dir, path.lstrip("/"))
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not any(within(base_dir / sub, p) for sub in subdirs):
        allowed = " and ".join(f"{s}/" for s in subdirs)
        raise HTTPException(400, f"only {allowed} files are served")
    if not p.is_file():
        raise HTTPException(404, f"no file {path!r}")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, filename=p.name)
