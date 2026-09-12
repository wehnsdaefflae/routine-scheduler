"""LLM task manager reconcile endpoint: a snapshot of open processes + in-flight/recent tasks.

The overlay streams live via the `llm_task`/`llm_process` bus events (see llm_tasks.TaskCenter);
it fetches this snapshot on boot and after an SSE reconnect, since the bus drops events for a
slow subscriber. The TaskCenter lives on app.state (set in app.py's lifespan).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["llm-tasks"])


@router.get("/llm-tasks")
async def llm_tasks(request: Request) -> dict:
    # async on purpose: this reads an in-memory snapshot and nothing else, and as a sync
    # handler it queued behind every slow request for a threadpool token — 572 times over
    # 30 s on 2026-09-12 for a payload of 27 bytes.
    center = getattr(request.app.state, "llm_tasks", None)
    if center is None:
        return {"processes": [], "tasks": []}
    return center.snapshot()
