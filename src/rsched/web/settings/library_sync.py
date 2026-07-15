"""Settings: the scheduled library sync job (enabled + friendly schedule + run-now).

The job itself is src/rsched/library_sync.py — a plain daemon job, not a routine. This
router edits its `library_sync:` block in config.yaml, live-patches the running server,
and exposes the last outcome the job wrote to .control/library-sync.json.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import library_sync, schedule
from ...config import LibrarySyncConfig
from .common import server_of, update_config

router = APIRouter()


def _payload(request: Request) -> dict:
    server = server_of(request)
    ls = server.library_sync
    sched = request.app.state.scheduler
    nxt = getattr(sched, "sync_next", None)
    return {"enabled": ls.enabled, "cron": ls.cron,
            "schedule_friendly": schedule.cron_to_friendly(ls.cron),
            "schedule_text": schedule.describe(ls.cron),
            "next_fire": nxt.isoformat() if nxt else None,
            "last": library_sync.read_status(server)}


class LibrarySyncBody(BaseModel):
    enabled: bool | None = None
    schedule: dict | None = None   # {"friendly": {...}} — converted to cron server-side


@router.get("/settings/library-sync")
def get_library_sync(request: Request) -> dict:
    return _payload(request)


@router.put("/settings/library-sync")
def put_library_sync(request: Request, body: LibrarySyncBody) -> dict:
    server = server_of(request)
    ls = server.library_sync
    enabled = ls.enabled if body.enabled is None else body.enabled
    cron, tz = ls.cron, ls.tz
    if body.schedule and "friendly" in body.schedule:
        try:
            cron = schedule.friendly_to_cron(body.schedule["friendly"])
        except ValueError as exc:
            raise HTTPException(400, f"invalid schedule: {exc}") from exc
        tz = schedule.server_tz()
    block = {"enabled": enabled, "cron": cron, "tz": tz}
    update_config(request, lambda raw: raw.update(library_sync=block))
    server.library_sync = LibrarySyncConfig(**block)
    request.app.state.scheduler.rescan()
    return _payload(request)


@router.post("/settings/library-sync/run")
async def run_library_sync(request: Request) -> dict:
    """Run one sync right now (off-loop; the scheduled job path, same code)."""
    return await asyncio.to_thread(library_sync.run_sync, server_of(request))
