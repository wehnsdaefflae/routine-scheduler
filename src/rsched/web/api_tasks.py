"""A routine's TASKS on its page (rsched/tasks.py, docs/tasks.md): the list as the store holds it,
what the current or last run owed and did, and the operator's hand on a task's state.

Read anywhere a routine is read. The ONE write — pausing, resuming, finishing a task, setting
its wake date or quiet limit, rewording it — is the operator's and lands only between runs: a
live run holds the ledger it decided at boot, and the gate that holds its finish reads the store,
so an edit landing mid-run would change what the run owes under it (409, like every other
between-runs edit, `guard_not_active`). Creating and deleting tasks is the run's job, done with
the `task` action as work comes up; the page is where a person steers the list, not keeps it.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import tasks
from ..grants import effective_settings, normalize_capabilities
from ..ids import now_iso
from .routines_common import _info, guard_not_active

router = APIRouter(tags=["routines"])


def layer_on(cfg) -> bool:
    """Does this routine keep tasks — the setting, read the way the run policy reads it."""
    return effective_settings(normalize_capabilities(cfg.capabilities)[0])["tasks"] == "on"


@router.get("/routines/{slug}/tasks")
def routine_tasks(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    doc = tasks.load(info.cfg.dir)
    ledger = doc.get("run") or {}
    due = {d.get("id"): d.get("why") or [] for d in ledger.get("due") or []}
    done = ledger.get("done") or {}
    watched = tasks.checks_by_task(list(info.cfg.run_gate.checks))
    rows = [{**{k: v for k, v in t.items() if k != "history"},
             "workspace": tasks.workspace_rel(t),
             "checks": watched.get(t["id"], []),
             "history": (t.get("history") or [])[:5],
             "due": t["id"] in due, "why": due.get(t["id"], []),
             "done": done.get(t["id"]), "open": t["id"] == ledger.get("open")}
            for t in doc["tasks"]]
    return {"enabled": layer_on(info.cfg), "tasks": rows,
            "run": {k: ledger.get(k) for k in ("run_id", "started", "ended", "open")},
            "deleted": [{"id": t.get("id"), "title": t.get("title"), "deleted": t.get("deleted")}
                        for t in doc.get("deleted") or []],
            "problems": tasks.problems(info.cfg.dir)}


class TaskPatch(BaseModel):
    state: str | None = None
    title: str | None = Field(None, max_length=tasks.TITLE_MAX)
    brief: str | None = Field(None, max_length=tasks.BRIEF_MAX)
    wake: str | None = None            # "" clears it
    quiet_days: int | None = Field(None, ge=0, le=365)   # 0 clears it


@router.patch("/routines/{slug}/tasks/{tid}")
def patch_task(request: Request, slug: str, tid: str, body: TaskPatch) -> dict:
    info = _info(request, slug)
    guard_not_active(request, info)
    if body.state is not None and body.state not in tasks.STATES:
        raise HTTPException(422, f"state is one of {', '.join(tasks.STATES)}")
    if body.wake:
        try:
            date.fromisoformat(body.wake)
        except ValueError:
            raise HTTPException(422, "wake is a date YYYY-MM-DD, or empty to clear it") from None
    with tasks.editing(info.cfg.dir) as doc:
        task = tasks.find(doc, tid)
        if task is None:
            raise HTTPException(404, f"{slug} has no task {tid!r}")
        changed = []
        for field in ("state", "title", "brief"):
            if (value := getattr(body, field)) is not None and value != task.get(field):
                task[field] = value
                changed.append(field)
        if body.state in ("paused", "done"):
            task.pop("carry", None)        # nothing is owed by a task you set aside
        if body.wake is not None:
            if body.wake:
                task["wake"] = body.wake
            else:
                task.pop("wake", None)
            changed.append("wake")
        if body.quiet_days is not None:
            if body.quiet_days:
                task["quiet_days"] = body.quiet_days
            else:
                task.pop("quiet_days", None)
            changed.append("quiet_days")
        if changed:
            task["updated"] = now_iso()
    return {"ok": True, "id": tid, "changed": changed}
