"""A routine's FILES: its artifacts and its recipe tree, read and written through the web.

Split out of `api_routines.py` when the write guard below pushed it past 440 lines. One
module, because the two halves answer the same question — which bytes under a routine dir a
person may look at or change from the console — and the answer is not "all of them":

- `artifacts/` is servable raw and deletable, and ONLY `artifacts/`;
- the recipe tree is readable and writable as JSON, and the files with a validated OWNER are
  refused by name (`NOT_EDITABLE_HERE`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..paths import atomic_write, resolve_rel
from . import artifacts
from .routines_common import _git_commit, _info, queue_or_apply

router = APIRouter(tags=["routines"])


@router.get("/routines/{slug}/artifacts")
def list_artifacts(request: Request, slug: str) -> list[dict]:
    """Everything under <routine>/artifacts/ — the routine's deliverables, newest first
    (the conversations panel's counterpart).
    """
    info = _info(request, slug)
    return artifacts.list_artifacts(info.cfg.dir)


@router.delete("/routines/{slug}/artifacts")
def delete_artifact(request: Request, slug: str, path: str) -> dict:
    """Remove one artifact from the sidebar (user order 2026-08-14). artifacts/ only."""
    info = _info(request, slug)
    return artifacts.delete_artifact(info.cfg.dir, path)


@router.get("/routines/{slug}/artifact")
def get_artifact(request: Request, slug: str, path: str):
    """Serve one artifact raw (blob-rendered client-side). ONLY artifacts/ is servable
    here — routine config/recipe reads stay on the JSON /file endpoint.
    """
    info = _info(request, slug)
    return artifacts.serve_file(info.cfg.dir, path)


@router.get("/routines/{slug}/file")
def get_routine_file(request: Request, slug: str, path: str) -> dict:
    info = _info(request, slug)
    try:
        p = resolve_rel(info.cfg.dir, path)
        return {"path": path, "content": p.read_text(encoding="utf-8")}
    except (PermissionError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


class RoutineFileBody(BaseModel):
    path: str
    content: str


#: Files under a routine dir that this endpoint may NOT write, and who owns each. The
#: recipe is a routine's own (materialized in) and editable here; everything below has a
#: validated owner, and a raw text PUT is a second writer around it. `routine.yaml` is the
#: sharp one: it would bypass `RoutinePatch`'s `extra="forbid"`, the permission floor, the
#: domain strip, `scheduler.rescan()` and the F337 live-run signal — a typo'd `permisions:`
#: key written verbatim while the fire table stays stale and the live run is told nothing.
NOT_EDITABLE_HERE: tuple[tuple[str, str], ...] = (
    ("routine.yaml", "PATCH /api/routines/{slug}"),
    ("state/stopping.json", "PUT /api/routines/{slug}/stopping"),
    # The engine owns `.memory/INDEX.md` (compaction._build_index) and nothing else under
    # `.memory/` — so INDEX.md is the line, not the tree. Refusing the whole tree would take
    # away the operator's only surface for a memory note and offer nothing in its place.
    (".memory/INDEX.md", "the engine (compaction writes the archive index)"),
    (".git/", "git"),
    ("runs/", "the engine"),
    ("inbox/", "the message endpoints"),
    ("questions/", "the Decisions page"),
)


def recipe_editable(rel: str) -> str:
    """"" when this relative path may be written through the file endpoint, else the owner
    that holds it. An entry ending in "/" claims its whole subtree; any other entry names
    exactly one file.
    """
    norm = str(rel).strip().replace("\\", "/").removeprefix("./").lstrip("/")
    for name, owner in NOT_EDITABLE_HERE:
        subtree = name.endswith("/")
        if norm == name.rstrip("/") or (subtree and norm.startswith(name)):
            return owner
    return ""


@router.put("/routines/{slug}/file")
def put_routine_file(request: Request, slug: str, body: RoutineFileBody) -> dict:
    """Edit one of the routine's RECIPE files — main.md, a stage module, a script, a note.
    A routine owns its recipe (materialized in), so those ARE editable here. This is the
    USER editing via the web (queued while a run is active) — distinct from a run, which may
    never write its own recipe or config.

    Its config and the engine-owned state are NOT editable here (`NOT_EDITABLE_HERE`): each
    has a validated endpoint of its own, and this one has no validation to offer.
    """
    info = _info(request, slug)
    # validate the path up front so a bad path is a 400 NOW, not a silent replay failure
    try:
        resolve_rel(info.cfg.dir, body.path)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if owner := recipe_editable(body.path):
        raise HTTPException(400, f"{body.path} is not editable here — it belongs to {owner}")

    def _apply() -> dict:
        p = resolve_rel(info.cfg.dir, body.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, body.content)
        _git_commit(info.cfg.dir, f"edit {body.path} via web")
        return {"ok": True}

    # D78-A: queue while a run is active (apply at run end) instead of a 409 busy toast
    return queue_or_apply(request, info, "file",
                          {"path": body.path, "content": body.content}, _apply)
