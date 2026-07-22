"""Server-process settings: the scalar ServerConfig knobs that are safe to change at
runtime — the util sandbox mode, run concurrency, the registry rescan cadence, and the
OAuth-app client id. The homes / bind / port / auth token stay install-time (config.yaml
+ a redeploy): they decide where data lives and how the socket is served, not day-to-day
behaviour, so the UI deliberately does not edit them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...config import load_server_config
from .common import server_of, update_config

router = APIRouter()

SANDBOX_MODES = ("strict", "permissive", "off")


class ServerBody(BaseModel):
    sandbox: str | None = None
    max_concurrent_runs: int | None = None
    registry_rescan_s: int | None = None
    github_client_id: str | None = None


@router.get("/settings/server")
def get_server(request: Request) -> dict:
    s = server_of(request)
    return {"sandbox": s.sandbox, "max_concurrent_runs": s.max_concurrent_runs,
            "registry_rescan_s": s.registry_rescan_s, "github_client_id": s.github_client_id}


@router.put("/settings/server")
def set_server(request: Request, body: ServerBody) -> dict:
    """Persist the runtime knobs to config.yaml and mirror them onto the live ServerConfig.
    sandbox (next util call) and registry_rescan_s (next scan) take effect immediately;
    max_concurrent_runs sizes the run semaphore at daemon startup, so it needs a restart.
    """
    updates = body.model_dump(exclude_none=True)
    if "sandbox" in updates and updates["sandbox"] not in SANDBOX_MODES:
        raise HTTPException(400, f"sandbox must be one of {SANDBOX_MODES}")
    if "max_concurrent_runs" in updates and updates["max_concurrent_runs"] < 1:
        raise HTTPException(400, "max_concurrent_runs must be at least 1")
    if "registry_rescan_s" in updates and updates["registry_rescan_s"] < 1:
        raise HTTPException(400, "registry_rescan_s must be at least 1 second")
    if not updates:
        return {"ok": True, "updated": []}
    path = update_config(request, lambda raw: raw.update(updates))
    fresh, _ = load_server_config(path)
    s = server_of(request)
    s.sandbox = fresh.sandbox
    s.max_concurrent_runs = fresh.max_concurrent_runs
    s.registry_rescan_s = fresh.registry_rescan_s
    s.github_client_id = fresh.github_client_id
    return {"ok": True, "updated": list(updates),
            "restart_for": ["max_concurrent_runs"] if "max_concurrent_runs" in updates else []}
