"""OAuth connect flow (Settings → Connections).

Auth-code + PKCE, uniform across providers: `authorize-start` mints a `state` + PKCE verifier and
returns the provider's authorize URL; the user consents in their browser; the provider redirects to
the PUBLIC `/oauth/callback` (mounted WITHOUT the bearer dependency in app.py — a redirect carries
no auth header, so the unguessable per-flow `state` is the CSRF guard); the callback exchanges the
code and writes the connection via the daemon-owned store. `router` is the authed CRUD the Settings
card uses; `callback_router` is the one public route.

Pending flows live in a process-local dict (like the GitHub device flow) — lost on restart, which
only means an in-flight consent must be restarted, never a stored token.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ...oauth import providers, store
from ...oauth.store import Connection
from .common import server_of, update_config

log = logging.getLogger("rsched.oauth")

router = APIRouter()            # authed → /api/settings/oauth/...
callback_router = APIRouter()   # UNAUTHENTICATED public redirect target (wired in app.py)

FLOW_TTL_S = 600
_flows: dict[str, dict] = {}      # flow_id → pending-flow state
_state_index: dict[str, str] = {}  # state → flow_id (the callback looks up by state)


def _prune() -> None:
    now = time.time()
    for fid in [f for f, e in _flows.items() if e["expires_at"] < now]:
        _state_index.pop(_flows[fid].get("state", ""), None)
        _flows.pop(fid, None)


def _pkce() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _redirect_uri(request: Request) -> str:
    base = server_of(request).public_url.rstrip("/")
    if not base:
        raise HTTPException(400, "set the instance public_url in Settings first — it is the OAuth "
                                 "redirect target (e.g. your Tailscale https URL)")
    return f"{base}/oauth/callback"


# ---- authed CRUD (Settings → Connections) -----------------------------------------------
@router.get("/settings/oauth")
def oauth_status(request: Request) -> dict:
    public_url = server_of(request).public_url
    provs = []
    for pid in providers.provider_ids():
        prov = providers.get_provider(pid)
        if prov is None:
            continue
        cid_key, secret_key = providers.creds_secret_keys(pid)
        provs.append({"id": pid, "name": prov.name, "expiring": prov.expiring,
                      "configured": providers.client_creds(pid) is not None,
                      "console_url": prov.console_url,
                      "client_id_key": cid_key, "client_secret_key": secret_key})
    return {"public_url": public_url, "public_url_set": bool(public_url),
            "providers": provs, "connections": store.list_connections()}


class PublicUrl(BaseModel):
    public_url: str


@router.put("/settings/oauth/public-url")
def set_public_url(request: Request, body: PublicUrl) -> dict:
    """Set the instance's external base URL (the OAuth redirect target). Persisted to config.yaml
    and applied live so the very next authorize-start uses it.
    """
    url = body.public_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "public_url must start with http:// or https://")
    update_config(request, lambda raw: raw.update({"public_url": url}))
    server_of(request).public_url = url
    return {"ok": True, "public_url": url}


class AuthorizeStart(BaseModel):
    account: str


@router.post("/settings/oauth/{provider}/authorize-start")
def authorize_start(request: Request, provider: str, body: AuthorizeStart) -> dict:
    prov = providers.get_provider(provider)
    if prov is None:
        raise HTTPException(404, f"unknown provider {provider!r}")
    account = body.account.strip()
    if not account:
        raise HTTPException(400, "an account label is required")
    creds = providers.client_creds(provider)
    if creds is None:
        cid_key, _ = providers.creds_secret_keys(provider)
        raise HTTPException(400, f"set {cid_key} (and its secret) in Settings → Secrets first")
    redirect_uri = _redirect_uri(request)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    flow_id = secrets.token_urlsafe(8)
    _prune()
    _flows[flow_id] = {"provider": provider, "account": account, "code_verifier": verifier,
                       "redirect_uri": redirect_uri, "state": state, "status": "pending",
                       "error": "", "expires_at": time.time() + FLOW_TTL_S}
    _state_index[state] = flow_id
    params = {"client_id": creds.client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "state": state}
    if prov.uses_pkce:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    if prov.default_scopes:
        params["scope"] = " ".join(prov.default_scopes)
    params.update(dict(prov.authorize_extra))
    return {"flow_id": flow_id, "authorize_url": f"{prov.authorize_url}?{urlencode(params)}"}


@router.get("/settings/oauth/flow/{flow_id}")
def oauth_flow(flow_id: str) -> dict:
    """Poll a pending flow's status (set by the callback): pending | connected | error."""
    _prune()
    entry = _flows.get(flow_id)
    if entry is None:
        raise HTTPException(404, "unknown or expired flow — start again")
    return {"status": entry["status"], "error": entry.get("error", ""),
            "provider": entry["provider"], "account": entry["account"]}


@router.delete("/settings/oauth/{provider}/{account}")
def oauth_delete(provider: str, account: str) -> dict:
    if not store.delete_connection(provider, account):
        raise HTTPException(404, f"no connection {provider}:{account}")
    return {"ok": True}


# ---- the token exchange -----------------------------------------------------------------
def _exchange(entry: dict, code: str) -> Connection:
    provider = entry["provider"]
    prov = providers.get_provider(provider)
    creds = providers.client_creds(provider)
    if prov is None or creds is None:
        raise RuntimeError("provider or credentials are no longer configured")
    data: dict[str, str] = {"grant_type": "authorization_code", "code": code,
                            "redirect_uri": entry["redirect_uri"]}
    if prov.uses_pkce:
        data["code_verifier"] = entry["code_verifier"]
    auth: tuple[str, str] | None = None
    if prov.exchange_auth == "basic":
        auth = (creds.client_id, creds.client_secret)
    else:
        data["client_id"] = creds.client_id
        if creds.client_secret:
            data["client_secret"] = creds.client_secret
    if prov.exchange_encoding == "json":
        resp = httpx.post(prov.token_url, json=data, auth=auth,
                          headers={"Accept": "application/json"}, timeout=20)
    else:
        resp = httpx.post(prov.token_url, data=data, auth=auth,
                          headers={"Accept": "application/json"}, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"token endpoint returned HTTP {resp.status_code}")
    return _connection_from(provider, entry["account"], prov, resp.json())


def _connection_from(provider: str, account: str, prov: providers.Provider,
                     payload: dict) -> Connection:
    access = payload.get("access_token") or ""
    if not access:
        raise RuntimeError("token response carried no access_token")
    expires_in = payload.get("expires_in")
    expires_at = time.time() + float(expires_in) if (prov.expiring and expires_in) else 0.0
    scope = payload.get("scope") or ""
    scopes = scope.split() if isinstance(scope, str) else [str(s) for s in scope]
    label = (payload.get("workspace_name")
             or (payload.get("team") or {}).get("name") or account)
    return Connection(provider=provider, account=account, access_token=access,
                      refresh_token=payload.get("refresh_token") or "", expires_at=expires_at,
                      scopes=scopes, obtained_at=time.time(), label=str(label))


# ---- the public callback ----------------------------------------------------------------
_PAGE = ('<!doctype html><meta charset="utf-8"><title>{title}</title>'
         '<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;text-align:center">'
         "<h2>{title}</h2><p>{body}</p>"
         '<p style="color:#888">You can close this tab and return to Settings.</p></body>')


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=title, body=body), status_code=status)


@callback_router.get("/oauth/callback")
def oauth_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    """The provider redirect lands here (unauthenticated). `state` (unguessable, per-flow, TTL'd)
    is the CSRF guard; on a match we exchange the code and store the connection. Nothing sensitive
    is echoed or logged.
    """
    _prune()
    flow_id = _state_index.pop(state, None) if state else None
    entry = _flows.get(flow_id) if flow_id else None
    if entry is None:
        log.warning("oauth callback rejected: unknown or expired state")
        return _page("Connection failed", "This authorization link is unknown or expired.", 400)
    if error:
        entry["status"], entry["error"] = "error", error
        log.warning("oauth callback: provider reported an error for %s", entry["provider"])
        # the ONE spot external text reaches an HTML page — escape it (query-carried)
        import html
        return _page("Connection failed",
                     f"The provider reported an error: {html.escape(error[:200])}.", 400)
    if not code:
        entry["status"], entry["error"] = "error", "no authorization code returned"
        return _page("Connection failed", "No authorization code was returned.", 400)
    try:
        store.set_connection(_exchange(entry, code))
    except Exception as exc:  # any exchange failure becomes one generic user-facing page
        entry["status"], entry["error"] = "error", str(exc)
        log.warning("oauth callback: token exchange failed for %s: %s", entry["provider"], exc)
        return _page("Connection failed", "Could not exchange the authorization code.", 502)
    entry["status"] = "connected"
    return _page("Connected", f"Your {entry['provider']} account is connected.")
