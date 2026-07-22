"""Detached background tasks of a conversation (the `detach` action): list, launch,
cancel, and the delete-time teardown. Split out of api_conversations — the routes stay
under /conversations/{slug}/background, so the UI contract is unchanged; the daemon's
DetachedManager (daemon/detached.py) owns the lifecycle, this router only reads
background_home and drops intents into its `.requests/`.
"""

from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request

from .. import registry
from ..config import load_routine
from ..daemon.runner import abort_process
from ..ids import background_task_id
from ..paths import atomic_write_json, read_json

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
    from .api_conversations import conversation_info

    conversation_info(request, slug)   # 404 if the conversation is gone
    return list_background_rows(request, slug)


@router.post("/conversations/{slug}/background")
def launch_background(request: Request, slug: str, prompt: Annotated[str, Form()],
                      workflow: Annotated[str, Form()] = "",
                      label: Annotated[str, Form()] = "") -> dict:
    """Drop a detached-task intent for the DetachedManager to pick up next tick. Mirrors what
    the engine `detach` action does — exposed so a human (or a test) can launch one directly.
    """
    from .api_conversations import conversation_info

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


@router.post("/conversations/{slug}/background/{taskid}/cancel")
async def cancel_background(request: Request, slug: str, taskid: str) -> dict:
    """Abort a running detached task. Falls back to signalling the recorded pid for a task that
    survived a daemon restart (no longer in the runner's active set), mirroring the run abort.
    """
    server = request.app.state.server
    task_dir = server.background_home / taskid
    cfg, _ = load_routine(task_dir) if (task_dir / "routine.yaml").exists() else (None, [])
    if cfg is None or (cfg.owner or {}).get("slug") != slug:
        raise HTTPException(404, f"no background task {taskid!r} for conversation {slug!r}")
    runner = request.app.state.runner
    ok = await runner.abort(taskid)
    if not ok:  # not daemon-owned (survived a restart) — signal the last run's recorded pid
        last = registry.run_index(task_dir, taskid)
        st = read_json(last[0].dir / "status.json") if last else None
        pid = st.get("pid") if isinstance(st, dict) else None
        ok = await abort_process(pid, last[0].dir, last[0].run_id) if last else False
    return {"ok": True, "cancelled": ok}


async def teardown_background(request: Request, slug: str) -> None:
    """On conversation delete: abort + remove its detached tasks (pid fallback for a task that
    outlived a restart), reusing the run abort path.
    """
    runner = request.app.state.runner
    for taskid, ti in _background_tasks(request, slug):
        if not await runner.abort(taskid):
            last = ti.last_run
            st = read_json(last.dir / "status.json") if last else None
            pid = st.get("pid") if isinstance(st, dict) else None
            if last and pid:
                await abort_process(pid, last.dir, last.run_id)
        shutil.rmtree(ti.cfg.dir, ignore_errors=True)
