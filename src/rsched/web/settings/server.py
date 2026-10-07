"""Server-process settings: the scalar ServerConfig knobs that are safe to change at
runtime — the util sandbox mode, run concurrency, the registry rescan cadence, the
OAuth-app client id, the address the shared browser can be watched at, and the two addresses
of the agent desktops (their broker and their noVNC page). The homes /
bind / port / auth token stay install-time (config.yaml + a redeploy): they decide where
data lives and how the socket is served, not day-to-day behaviour, so the UI deliberately
does not edit them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from ...config import ServerConfig
from .common import reload_into, server_of, update_config

router = APIRouter()


class ServerBody(BaseModel):
    sandbox: str | None = None
    max_concurrent_runs: int | None = None
    registry_rescan_s: int | None = None
    github_client_id: str | None = None
    # The noVNC page showing the shared browser. An address of the DEPLOYMENT, like
    # public_url — whether that port is reachable depends on the host's networking, so
    # nothing here can derive it and it is set once, by hand.
    browser_view_url: str | None = None
    # The agent desktops' broker (the fleet the Desktops page lists) and their noVNC page
    # (every desktop's screen, relayed at /desktop-view). The same kind of address, set the
    # same way; the console shows the desktops only once both are.
    desktop_broker_url: str | None = None
    desktop_view_url: str | None = None


#: The address fields, each with the example its refusal offers. One check for all three: they
#: are the same kind of value (an address a server-side relay dials), and a copy per field is
#: where one of them would start accepting a bare host.
URL_FIELDS = {
    "browser_view_url": "http://host:6080/vnc.html",
    "desktop_broker_url": "http://172.30.7.20:8790",
    "desktop_view_url": "http://172.30.7.20:6080/vnc.html",
}


@router.get("/settings/server")
def get_server(request: Request) -> dict:
    s = server_of(request)
    return {"sandbox": s.sandbox, "max_concurrent_runs": s.max_concurrent_runs,
            "registry_rescan_s": s.registry_rescan_s, "github_client_id": s.github_client_id,
            "browser_view_url": s.browser_view_url,
            "desktop_broker_url": s.desktop_broker_url, "desktop_view_url": s.desktop_view_url}


@router.put("/settings/server")
def set_server(request: Request, body: ServerBody) -> dict:
    """Persist the runtime knobs to config.yaml and mirror them onto the live ServerConfig.
    sandbox (next util call) and registry_rescan_s (next scan) take effect immediately;
    max_concurrent_runs sizes the run semaphore at daemon startup, so it needs a restart.
    """
    updates = body.model_dump(exclude_none=True)
    # ServerConfig's own fields judge each value — the sandbox vocabulary and every bound —
    # so the config a restart loads can never refuse what this route saved, and no bound
    # is restated here to drift from the one the loader enforces.
    try:
        ServerConfig.model_validate(updates)
    except ValidationError as exc:
        raise HTTPException(400, "; ".join(
            f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())) from exc
    for field, example in URL_FIELDS.items():
        if not updates.get(field):
            continue
        url = str(updates[field]).strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, f"{field} must start with http:// or https:// — it is an "
                                     f"address this console dials, e.g. {example}")
        updates[field] = url
    if not updates:
        return {"ok": True, "updated": []}
    path = update_config(request, lambda raw: raw.update(updates))
    reload_into(request, path, "sandbox", "max_concurrent_runs", "registry_rescan_s",
                "github_client_id", *URL_FIELDS)
    return {"ok": True, "updated": list(updates),
            "restart_for": ["max_concurrent_runs"] if "max_concurrent_runs" in updates else []}
