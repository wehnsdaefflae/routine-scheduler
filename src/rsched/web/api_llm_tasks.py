"""LLM task manager reconcile endpoint: a snapshot of open processes + in-flight/recent tasks.

The overlay streams live via the `llm_task`/`llm_process` bus events (see llm_tasks.TaskCenter);
it fetches this snapshot on boot and after an SSE reconnect, since the bus drops events for a
slow subscriber. The TaskCenter lives on app.state (set in app.py's lifespan)."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["llm-tasks"])


@router.get("/llm-tasks")
def llm_tasks(request: Request) -> dict:
    center = getattr(request.app.state, "llm_tasks", None)
    if center is None:
        return {"processes": [], "tasks": []}
    return center.snapshot()
