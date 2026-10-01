"""Detached background tasks of a conversation (the `detach` action): list, launch,
cancel, and the delete-time teardown. Split out of api_conversations — the routes stay
under /conversations/{slug}/background, so the UI contract is unchanged; the daemon's
DetachedManager (daemon/detached.py) owns the lifecycle, this router only reads
background_home and drops intents into its `.requests/`.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request

from .. import registry
from ..ids import background_task_id
from ..paths import atomic_write_json

router = APIRouter(tags=["background"])


def _background_tasks(request: Request, owner_slug: str) -> list[tuple[str, registry.RoutineInfo]]:
    """(taskid, info) for every detached task owned by this conversation."""
    server = request.app.state.server
    catalog = registry.scan(server, server.background_home)
    return [(tid, ti) for tid, ti in catalog.items()
            if (ti.cfg.owner or {}).get("slug") == owner_slug]


def list_background_rows(request: Request, slug: str) -> list[dict]:
    out: list[dict] = []
    for taskid, ti in _background_tasks(request, slug):
        last = ti.last_run
        out.append({"taskid": taskid, "label": ti.cfg.name or taskid,
                    "state": last.state if last else "pending",
                    "run_id": last.run_id if last else "",
                    "summary": (last.summary[:200] if last else ""),
                    "delivered": (ti.cfg.dir / "delivered.json").exists()})
    out.sort(key=lambda r: r["run_id"])
    return out


@router.get("/conversations/{slug}/background")
def list_background(request: Request, slug: str) -> list[dict]:
    """The detached tasks this conversation launched — for the run-view rail and monitoring."""
    from .conversations_common import conversation_info

    conversation_info(request, slug)   # 404 if the conversation is gone
    return list_background_rows(request, slug)


@router.post("/conversations/{slug}/background")
def launch_background(request: Request, slug: str, prompt: Annotated[str, Form()],
                      workflow: Annotated[str, Form()] = "",
                      label: Annotated[str, Form()] = "") -> dict:
    """Drop a detached-task intent for the DetachedManager to pick up next tick. Mirrors what
    the engine `detach` action does — exposed so a human (or a test) can launch one directly.
    """
    from .conversations_common import conversation_info

    info = conversation_info(request, slug)
    if not prompt.strip():
        raise HTTPException(400, "empty prompt")
    server = request.app.state.server
    taskid = background_task_id(slug)
    reqs = server.background_home / ".requests"
    reqs.mkdir(parents=True, exist_ok=True)
    atomic_write_json(reqs / f"{taskid}.json",
                      {"taskid": taskid, "prompt": prompt.strip(),
                       "workflow": (workflow.strip() or "general-task"),
                       "label": (label.strip() or "background task"),
                       "owner": {"slug": slug, "dir": str(info.cfg.dir)}})
    return {"ok": True, "taskid": taskid}


async def _abort(request: Request, taskid: str, ti: registry.RoutineInfo) -> bool:
    """Abort one detached task through the run abort path, falling back to its recorded pid
    when it outlived a daemon restart (no longer in the runner's active set). True when a
    process was told to stop.
    """
    from .api_run_control import abort_with_fallback

    runner = request.app.state.runner
    last = ti.last_run
    return (await abort_with_fallback(runner, taskid, last.dir) if last
            else await runner.abort(taskid))


@router.post("/conversations/{slug}/background/{taskid}/cancel")
async def cancel_background(request: Request, slug: str, taskid: str) -> dict:
    """Abort a running detached task this conversation owns."""
    server = request.app.state.server
    ti = registry.info(server, server.background_home, taskid)
    if ti is None or (ti.cfg.owner or {}).get("slug") != slug:
        raise HTTPException(404, f"no background task {taskid!r} for conversation {slug!r}")
    if not await _abort(request, taskid, ti):
        # honesty: the UI used to toast "cancelling…" off ok:true while nothing died
        raise HTTPException(409, "no live process for this task — it may already be done")
    return {"ok": True, "cancelled": True}


async def teardown_background(request: Request, slug: str) -> None:
    """On conversation delete: abort + remove its detached tasks."""
    for taskid, ti in _background_tasks(request, slug):
        await _abort(request, taskid, ti)
        await asyncio.to_thread(shutil.rmtree, ti.cfg.dir, ignore_errors=True)
