"""GitHub connect: device flow driven from the web UI, then handed to `gh` in the container.
The user never needs a container terminal: the UI shows a one-time code, they authorize in their
own browser, and the backend stores the token via `gh` (persisted in the mounted ~/.config/gh).
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .common import server_of

router = APIRouter()

GH_CLI_CLIENT_ID = "178c6fc778ccc68e1d6a"   # GitHub CLI's public OAuth app (device flow on)
_device_flows: dict[str, dict] = {}


def _gh_login() -> str | None:
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True,
                       timeout=15, check=False)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


@router.get("/settings/github")
def github_status(_request: Request) -> dict:
    if not shutil.which("gh"):
        return {"gh": False, "connected": False, "error": "the `gh` CLI is not in this environment"}
    login = _gh_login()
    return {"gh": True, "connected": bool(login), "login": login}


@router.post("/settings/github/device-start")
def github_device_start(request: Request) -> dict:
    if not shutil.which("gh"):
        raise HTTPException(400, "the `gh` CLI is not installed in this environment")
    client_id = server_of(request).github_client_id or GH_CLI_CLIENT_ID
    try:
        r = httpx.post("https://github.com/login/device/code",
                       data={"client_id": client_id, "scope": "repo"},
                       headers={"Accept": "application/json"}, timeout=15)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach github.com: {exc}") from exc
    if r.status_code != 200:
        raise HTTPException(502, f"github device/code failed: {r.text[:200]}")
    d = r.json()
    flow_id = secrets.token_urlsafe(8)
    verification_uri = d.get("verification_uri", "https://github.com/login/device")
    interval = d.get("interval", 5)
    # Keep the display fields so a reloaded UI can resume the SAME flow via GET (below) instead of
    # losing the one-time code — the device-flow state is then addressable as #/settings?flow=<id>.
    _device_flows[flow_id] = {"device_code": d["device_code"], "client_id": client_id,
                              "user_code": d["user_code"], "verification_uri": verification_uri,
                              "interval": interval,
                              "expires_at": time.time() + int(d.get("expires_in", 900))}
    return {"flow_id": flow_id, "user_code": d["user_code"], "verification_uri": verification_uri,
            "interval": interval, "expires_in": d.get("expires_in", 900)}


@router.get("/settings/github/device-flow/{flow_id}")
def github_device_flow(_request: Request, flow_id: str) -> dict:
    """Resume a pending device flow after a reload: return its still-valid code + URL, or 404 if
    it's unknown/expired (the UI then just shows the normal connect button).
    """
    flow = _device_flows.get(flow_id)
    remaining = int(flow["expires_at"] - time.time()) if flow else 0
    if not flow or remaining <= 0:
        _device_flows.pop(flow_id, None)
        raise HTTPException(404, "unknown or expired flow — start again")
    return {"flow_id": flow_id, "user_code": flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "interval": flow.get("interval", 5), "expires_in": remaining}


class DevicePoll(BaseModel):
    flow_id: str


@router.post("/settings/github/device-poll")
def github_device_poll(_request: Request, body: DevicePoll) -> dict:
    """Called by the UI every few seconds until the user authorizes; then store the token via gh."""
    flow = _device_flows.get(body.flow_id)
    if flow is None:
        raise HTTPException(404, "unknown or expired flow — start again")
    try:
        r = httpx.post("https://github.com/login/oauth/access_token",
                       data={"client_id": flow["client_id"], "device_code": flow["device_code"],
                             "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                       headers={"Accept": "application/json"}, timeout=15)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach github.com: {exc}") from exc
    d = r.json()
    if d.get("access_token"):
        _device_flows.pop(body.flow_id, None)
        return {"status": "connected", "login": _gh_store_token(d["access_token"])}
    err = d.get("error", "unknown")
    if err in ("authorization_pending", "slow_down"):
        return {"status": "pending"}
    _device_flows.pop(body.flow_id, None)
    return {"status": "error", "error": d.get("error_description") or err}


def _gh_store_token(token: str) -> str:
    """Store the OAuth token via gh (into the mounted ~/.config/gh) and wire git to use it."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    login = subprocess.run(
        ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https",
         "--with-token"],
        input=token, capture_output=True, text=True, timeout=30, env=env, check=False)
    if login.returncode != 0:
        raise HTTPException(502, f"gh auth login failed: {login.stderr.strip()[:200]}")
    subprocess.run(["gh", "auth", "setup-git"], capture_output=True, timeout=15, env=env,
                   check=False)
    return _gh_login() or "connected"
