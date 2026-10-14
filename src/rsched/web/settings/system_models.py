"""The two instance-wide model choices that read from the catalog: the SYSTEM model (the
scheduler's own helper calls) and the context-compaction model.

Split out of `endpoints.py`, which stores transports and the catalog — these two are
single-name pointers INTO the catalog, saved by their own routes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...config import EFFORT_LEVELS
from .common import reload_into, server_of, update_config

router = APIRouter(tags=["system-models"])


class SystemModelBody(BaseModel):
    name: str   # a catalog model name


class SystemEffortBody(SystemModelBody):
    effort: str = ""   # one of EFFORT_LEVELS; blank = the provider's default


@router.put("/settings/system-model")
def set_system_model(request: Request, body: SystemEffortBody) -> dict:
    """Set the ONE fallback model for machine work that isn't a routine yet — workflow
    generation/suggestion and the new-routine clarify flow. Setting it is what makes the
    instance 'llm_ready'; routines otherwise pick their own (also by catalog name).
    """
    s = server_of(request)
    if body.name not in s.models:
        raise HTTPException(400, f"unknown model {body.name!r} — add it to the catalog first")
    if body.effort and body.effort not in EFFORT_LEVELS:
        raise HTTPException(400, f"effort must be one of {EFFORT_LEVELS} (or blank)")

    def mutate(raw: dict) -> None:
        raw.update(system_model=body.name)
        raw.pop("system_effort", None)
        if body.effort:
            raw["system_effort"] = body.effort

    path = update_config(request, mutate)
    reload_into(request, path, "system_model", "system_effort")
    return {"ok": True, "system_model": s.system_model or None,
            "system_effort": s.system_effort or None}


@router.put("/settings/compaction-model")
def set_compaction_model(request: Request, body: SystemEffortBody) -> dict:
    """Choose background archival only, and how hard it reasons; an empty name restores
    Automatic routing (which uses the tool-call role's own model and effort).
    """
    s = server_of(request)
    if body.name and body.name not in s.models:
        raise HTTPException(400, f"unknown model {body.name!r} — add it to the catalog first")
    if body.effort and body.effort not in EFFORT_LEVELS:
        raise HTTPException(400, f"effort must be one of {EFFORT_LEVELS} (or blank)")

    def mutate(raw: dict) -> None:
        raw.update(compaction_model=body.name)
        raw.pop("compaction_effort", None)
        if body.name and body.effort:
            raw["compaction_effort"] = body.effort

    path = update_config(request, mutate)
    reload_into(request, path, "compaction_model", "compaction_effort")
    return {"ok": True, "compaction_model": s.compaction_model or None,
            "compaction_effort": s.compaction_effort or None}
