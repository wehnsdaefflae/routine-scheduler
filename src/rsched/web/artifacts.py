"""Shared artifact listing/serving — one implementation behind the routine AND the
conversation artifact panels (each router keeps only a thin handler on top).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..paths import resolve_rel, within


def list_artifacts(base_dir: Path) -> list[dict]:
    """Everything under <dir>/artifacts/ — the deliverables, newest first."""
    art = base_dir / "artifacts"
    out: list[dict] = []
    if art.is_dir():
        for p in art.rglob("*"):
            if p.is_file():
                st = p.stat()
                out.append({"path": str(p.relative_to(base_dir)), "name": p.name,
                            "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def serve_file(base_dir: Path, path: str,
               subdirs: tuple[str, ...] = ("artifacts",)) -> FileResponse:
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
