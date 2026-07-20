"""Summary/overview endpoint — each routine's latest finish message in ONE place.

A sibling to the Decisions inbox (api_questions): where Decisions collects the answers the
routines need FROM you, Summary collects the last thing each routine told you — its most
recent run's finish summary — so a glance says "what did everything I run last say". Backed
entirely by the filesystem via registry.scan (the same read-model the dashboard uses); the
only writable state is a small per-routine read-marker under routines_home/.control/, so an
item can be dismissed (marked read) and stays dismissed until that routine finishes a newer
run. Read-only otherwise: every call reflects the live run state with no cache.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..daemon import registry
from ..paths import atomic_write_json, read_json

router = APIRouter(tags=["summary"])


def _read_path(server):
    return server.routines_home / ".control" / "summary-read.json"


def _read_map(server) -> dict:
    data = read_json(_read_path(server))
    return data if isinstance(data, dict) else {}


def _latest(info) -> registry.RunInfo | None:
    """The run whose finish message we surface: the newest run that actually carries a
    summary (a still-running or summary-less run is not a 'latest finish message'), falling
    back to the newest run of any state so a fresh routine still shows a row.
    """
    runs = info.runs or []
    if not runs:
        return None
    for r in runs:                       # info.runs is newest-first
        if (r.summary or "").strip():
            return r
    return runs[0]


@router.get("/summary")
def summary(request: Request) -> list[dict]:
    """Latest finish message per routine, most-recently-updated first. `read` is true when
    the operator has dismissed THIS run's message (a newer run clears it automatically).
    """
    server = request.app.state.server
    read_map = _read_map(server)
    rows = []
    for slug, info in registry.scan(server).items():
        last = _latest(info)
        if last is None:
            continue
        rows.append({
            "slug": slug,
            "title": getattr(info.cfg, "name", "") or slug,
            "run_id": last.run_id,
            "ts": last.ts,
            "state": last.state,
            "outcome": getattr(last, "outcome", "") or "",
            "summary": last.summary or "",
            "updated": last.updated,
            "read": read_map.get(slug) == last.run_id,
        })
    rows.sort(key=lambda r: r["updated"] or r["ts"], reverse=True)
    return rows


class MarkRead(BaseModel):
    run_id: str
    read: bool = True


@router.post("/summary/{slug}/read")
def mark_read(request: Request, slug: str, body: MarkRead) -> dict:
    """Dismiss (or un-dismiss) a routine's current finish message. Stores the run_id the
    operator has seen; the GET compares it against the latest run so a newer run resurfaces.
    """
    server = request.app.state.server
    if slug not in registry.scan(server):
        raise HTTPException(404, f"no routine {slug!r}")
    read_map = _read_map(server)
    if body.read:
        read_map[slug] = body.run_id
    else:
        read_map.pop(slug, None)
    _read_path(server).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_read_path(server), read_map)
    return {"ok": True, "slug": slug, "read": body.read}
