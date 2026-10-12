"""A routine's JOBS on its page (rsched/jobs.py, docs/jobs.md): the list as the store holds it,
what the current or last run owed and did, and the operator's hand on a job's state.

Read anywhere a routine is read. The ONE write — pausing, resuming, finishing a job, setting
its wake date or quiet limit, rewording it, recording the routine it was before a merge
(`origin`) — is the operator's and lands only between runs: a
live run holds the ledger it decided at boot, and the gate that holds its finish reads the store,
so an edit landing mid-run would change what the run owes under it (409, like every other
between-runs edit, `guard_not_active`). Creating and deleting jobs is the run's job, done with
the `job` action as work comes up; the page is where a person steers the list, not keeps it.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import jobs
from ..grants import effective_settings, normalize_capabilities
from ..ids import now_iso
from .routines_common import _info, _state, guard_not_active

router = APIRouter(tags=["routines"])


def layer_on(cfg) -> bool:
    """Does this routine keep jobs — the setting, read the way the run policy reads it."""
    return effective_settings(normalize_capabilities(cfg.capabilities)[0])["jobs"] == "on"


@router.get("/routines/{slug}/jobs")
def routine_jobs(request: Request, slug: str) -> dict:
    info = _info(request, slug)
    doc = jobs.load(info.cfg.dir)
    ledger = doc.get("run") or {}
    due = {d.get("id"): d.get("why") or [] for d in ledger.get("due") or []}
    done = ledger.get("done") or {}
    watched = jobs.checks_by_job(list(info.cfg.run_gate.checks))
    rows = [{**{k: v for k, v in t.items() if k != "history"},
             "workspace": jobs.workspace_rel(t),
             "checks": watched.get(t["id"], []),
             "history": (t.get("history") or [])[:5],
             "due": t["id"] in due, "why": due.get(t["id"], []),
             "done": done.get(t["id"]), "open": t["id"] == ledger.get("open")}
            for t in doc["jobs"]]
    return {"enabled": layer_on(info.cfg), "jobs": rows,
            "run": {k: ledger.get(k) for k in ("run_id", "started", "ended", "open")},
            "deleted": [{"id": t.get("id"), "title": t.get("title"), "deleted": t.get("deleted")}
                        for t in doc.get("deleted") or []],
            "problems": jobs.problems(info.cfg.dir)}


class JobPatch(BaseModel):
    state: str | None = None
    title: str | None = Field(None, max_length=jobs.TITLE_MAX)
    brief: str | None = Field(None, max_length=jobs.BRIEF_MAX)
    wake: str | None = None            # "" clears it
    quiet_days: int | None = Field(None, ge=0, le=365)   # 0 clears it
    origin: str | None = None          # the routine this job was before a merge; "" clears it


@router.patch("/routines/{slug}/jobs/{tid}")
def patch_job(request: Request, slug: str, tid: str, body: JobPatch) -> dict:
    info = _info(request, slug)
    guard_not_active(request, info)
    if body.state is not None and body.state not in jobs.STATES:
        raise HTTPException(422, f"state is one of {', '.join(jobs.STATES)}")
    if body.wake:
        try:
            date.fromisoformat(body.wake)
        except ValueError:
            raise HTTPException(422, "wake is a date YYYY-MM-DD, or empty to clear it") from None
    if body.origin:
        home = _state(request).server.routines_home
        if body.origin == slug or not (home / body.origin / "routine.yaml").is_file():
            raise HTTPException(422, f"origin names the routine this job was before a merge — "
                                     f"{body.origin!r} is not another routine here")
    with jobs.editing(info.cfg.dir) as doc:
        job = jobs.find(doc, tid)
        if job is None:
            raise HTTPException(404, f"{slug} has no job {tid!r}")
        changed = []
        for field in ("state", "title", "brief"):
            if (value := getattr(body, field)) is not None and value != job.get(field):
                job[field] = value
                changed.append(field)
        if body.state in ("paused", "done"):
            job.pop("carry", None)        # nothing is owed by a job you set aside
        if body.wake is not None:
            if body.wake:
                job["wake"] = body.wake
            else:
                job.pop("wake", None)
            changed.append("wake")
        if body.quiet_days is not None:
            if body.quiet_days:
                job["quiet_days"] = body.quiet_days
            else:
                job.pop("quiet_days", None)
            changed.append("quiet_days")
        if body.origin is not None:
            if body.origin:
                job["origin"] = body.origin
            else:
                job.pop("origin", None)
            changed.append("origin")
        if changed:
            job["updated"] = now_iso()
    return {"ok": True, "id": tid, "changed": changed}
