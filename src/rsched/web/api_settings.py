"""Endpoint settings: CRUD over the config.yaml endpoints block + a live test call."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import subprocess
import time

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import secrets as secret_store
from ..config import ENDPOINT_KINDS, EndpointConfig, load_server_config
from ..endpoints import EndpointRegistry, make_endpoint
from ..endpoints.base import EndpointError
from ..schema_guard import SchemaViolation, parse_reply

router = APIRouter(tags=["settings"])

TEST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"],
               "properties": {"answer": {"type": "integer"}}}


def _server(request: Request):
    return request.app.state.server


def _config_path(request: Request):
    p = _server(request).source
    if p is None or not p.exists():
        raise HTTPException(500, "server config file not found")
    return p


def _rewrite_endpoints(request: Request, mutate) -> dict:
    path = _config_path(request)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    endpoints = raw.get("endpoints") or {}
    mutate(endpoints)
    raw["endpoints"] = endpoints
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    # live-reload the shared ServerConfig (daemon side; engine subprocesses read it fresh)
    fresh, problems = load_server_config(path)
    request.app.state.server.endpoints = fresh.endpoints
    request.app.state.server.default_roles = fresh.default_roles
    return {"ok": True, "problems": problems}


def _endpoint_view(name: str, ep: EndpointConfig) -> dict:
    return {"name": name, "kind": ep.kind, "base_url": ep.base_url,
            "key_env_file": ep.key_env_file, "key_var": ep.key_var,
            "schema_mode": ep.schema_mode, "context_chars": ep.context_chars,
            "temperature": ep.temperature, "has_inline_key": bool(ep.api_key)}


@router.get("/settings/endpoints")
def list_endpoints(request: Request) -> dict:
    server = _server(request)
    return {"endpoints": [_endpoint_view(n, e) for n, e in server.endpoints.items()],
            "default_roles": {r: {"endpoint": ref.endpoint, "model": ref.model}
                              for r, ref in server.default_roles.items()}}


# --- library repositories (workflows / fragments / global utils) ---------------------

def _remote_of(home) -> str:
    r = subprocess.run(["git", "-C", str(home), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


@router.get("/settings/libraries")
def list_libraries(request: Request) -> dict:
    s = _server(request)
    libs = [("workflows", s.library_home, s.library_remote),
            ("fragments", s.fragments_home, s.fragments_remote),
            ("utils", s.utils_home, s.utils_remote)]
    return {"libraries": [{"name": n, "home": str(h),
                           "remote": _remote_of(h) or r,
                           "exists": (h / ".git").is_dir()} for n, h, r in libs]}


class LibraryRemote(BaseModel):
    remote: str


@router.put("/settings/libraries/{name}")
def set_library_remote(request: Request, name: str, body: LibraryRemote) -> dict:
    s = _server(request)
    homes = {"workflows": (s.library_home, "library_remote"),
             "fragments": (s.fragments_home, "fragments_remote"),
             "utils": (s.utils_home, "utils_remote")}
    if name not in homes:
        raise HTTPException(404, f"unknown library {name!r}")
    home, cfg_key = homes[name]
    # write into config.yaml
    path = _config_path(request)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw[cfg_key] = body.remote
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    setattr(s, cfg_key, body.remote)
    # point the local repo's origin at it (best-effort)
    result = {"ok": True, "pushed": False}
    if body.remote and (home / ".git").is_dir():
        subprocess.run(["git", "-C", str(home), "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "-C", str(home), "remote", "add", "origin", body.remote], capture_output=True)
        push = subprocess.run(["git", "-C", str(home), "push", "-u", "origin", "main"],
                              capture_output=True, text=True, timeout=60)
        result["pushed"] = push.returncode == 0
        if push.returncode != 0:
            result["push_error"] = push.stderr.strip()[:200]
    return result


# --- scheduler source repository (where self-audit commits + pushes code) -------------

@router.get("/settings/source")
def get_source_repo(request: Request) -> dict:
    s = _server(request)
    home = s.source_repo
    is_git = (home / ".git").is_dir()
    branch = ""
    if is_git:
        r = subprocess.run(["git", "-C", str(home), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        branch = r.stdout.strip() if r.returncode == 0 else ""
    return {"home": str(home), "remote": _remote_of(home) or s.source_remote,
            "exists": is_git, "branch": branch or "main"}


@router.put("/settings/source")
def set_source_remote(request: Request, body: LibraryRemote) -> dict:
    s = _server(request)
    home = s.source_repo
    # persist into config.yaml
    path = _config_path(request)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["source_remote"] = body.remote
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    s.source_remote = body.remote
    # point origin at it — SAFE: set-url (add if absent), never remove; this is the live code repo
    result = {"ok": True, "pushed": False}
    if body.remote and (home / ".git").is_dir():
        set_url = subprocess.run(["git", "-C", str(home), "remote", "set-url", "origin", body.remote],
                                 capture_output=True, text=True)
        if set_url.returncode != 0:                     # no origin yet → add it
            subprocess.run(["git", "-C", str(home), "remote", "add", "origin", body.remote],
                           capture_output=True)
        push = subprocess.run(["git", "-C", str(home), "push", "-u", "origin", "HEAD"],
                              capture_output=True, text=True, timeout=60)
        result["pushed"] = push.returncode == 0
        if push.returncode != 0:
            result["push_error"] = push.stderr.strip()[:200]
    return result


class RemoteTest(BaseModel):
    remote: str


@router.post("/settings/test-remote")
def test_remote(_request: Request, body: RemoteTest) -> dict:
    """Validate that a git remote is reachable AND authorized, for the Settings 'Test' button.
    Runs `git ls-remote` with prompts disabled so a private repo without credentials fails fast
    (rather than hanging), and surfaces the git error verbatim (auth failure, no such repo, DNS)."""
    url = body.remote.strip()
    if not url:
        return {"ok": False, "error": "no remote URL configured"}
    # GIT_TERMINAL_PROMPT=0 → never block on a username/password prompt; fail with the auth error.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", url],
                           capture_output=True, text=True, timeout=30, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after 30s — host unreachable?"}
    if r.returncode == 0:
        branches = [ln.split("refs/heads/")[-1] for ln in r.stdout.splitlines() if ln.strip()]
        return {"ok": True, "branches": len(branches),
                "detail": f"reachable — {len(branches)} branch(es)" + (f": {branches[0]}…" if branches else "")}
    raw = r.stderr.strip() or "git ls-remote failed"
    last = raw.splitlines()[-1][:300]
    low = raw.lower()
    # actionable hints for the two errors users actually hit on first setup
    if any(s in low for s in ("could not read username", "authentication failed", "terminal prompts disabled")):
        return {"ok": False, "error": "authentication required — is it a private repo? run "
                "`gh auth login` in the container (see deploy/SETUP.md)", "detail": last}
    if "not found" in low:
        return {"ok": False, "error": "repository not found (or no access) — check the URL and auth",
                "detail": last}
    return {"ok": False, "error": last}


# --- GitHub connect: device flow driven from the web UI, then handed to `gh` in the container ---
# The user never needs a container terminal: the UI shows a one-time code, they authorize in their
# own browser, and the backend stores the token via `gh` (persisted in the mounted ~/.config/gh).

GH_CLI_CLIENT_ID = "178c6fc778ccc68e1d6a"   # GitHub CLI's public OAuth app (has device flow enabled)
_device_flows: dict[str, dict] = {}


def _gh_login() -> str | None:
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, timeout=15)
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
    client_id = _server(request).github_client_id or GH_CLI_CLIENT_ID
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
    _device_flows[flow_id] = {"device_code": d["device_code"], "client_id": client_id}
    return {"flow_id": flow_id, "user_code": d["user_code"],
            "verification_uri": d.get("verification_uri", "https://github.com/login/device"),
            "interval": d.get("interval", 5), "expires_in": d.get("expires_in", 900)}


class DevicePoll(BaseModel):
    flow_id: str


@router.post("/settings/github/device-poll")
def github_device_poll(_request: Request, body: DevicePoll) -> dict:
    """Called by the UI every few seconds until the user authorizes; then store the token via gh."""
    flow = _device_flows.get(body.flow_id)
    if not flow:
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
        ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--with-token"],
        input=token, capture_output=True, text=True, timeout=30, env=env)
    if login.returncode != 0:
        raise HTTPException(502, f"gh auth login failed: {login.stderr.strip()[:200]}")
    subprocess.run(["gh", "auth", "setup-git"], capture_output=True, timeout=15, env=env)
    return _gh_login() or "connected"


# --- central secrets store: one KEY=VALUE store injected into utils + endpoints + claude-cli -----
# Set any credential here (a util's token, a username, an API key, the Claude subscription token as
# CLAUDE_CODE_OAUTH_TOKEN). The engine injects it into every util + endpoint at run time. Values are
# write-only: the API returns key NAMES, never the values.

@router.get("/settings/secrets")
def list_secrets(request: Request) -> dict:
    from .. import utils_lib
    have = set(secret_store.secret_keys())
    # which env vars do the installed utils declare they need, and are they set yet?
    declared: dict[str, list[str]] = {}
    for u in utils_lib.list_utils(_server(request).utils_home):
        for var in u.get("secrets", []):
            declared.setdefault(var, []).append(u["name"])
    needed = [{"key": k, "utils": us, "set": k in have} for k, us in sorted(declared.items())]
    return {"keys": sorted(have), "needed": needed, "path": str(secret_store.secrets_path())}


class SecretBody(BaseModel):
    key: str
    value: str


@router.put("/settings/secrets")
def put_secret(_request: Request, body: SecretBody) -> dict:
    try:
        secret_store.set_secret(body.key.strip(), body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"cannot write the secrets store: {exc}") from exc
    return {"ok": True, "keys": secret_store.secret_keys()}


@router.delete("/settings/secrets/{key}")
def remove_secret(_request: Request, key: str) -> dict:
    if not secret_store.delete_secret(key):
        raise HTTPException(404, f"no secret {key!r}")
    return {"ok": True, "keys": secret_store.secret_keys()}


class EndpointBody(BaseModel):
    name: str
    kind: str
    base_url: str = ""
    api_key: str = ""
    key_env_file: str = ""
    key_var: str = ""
    schema_mode: str = "json_schema"
    context_chars: int = 100_000
    temperature: float | None = None


@router.post("/settings/endpoints")
@router.put("/settings/endpoints/{name}")
def upsert_endpoint(request: Request, body: EndpointBody, name: str | None = None) -> dict:
    if body.kind not in ENDPOINT_KINDS:
        raise HTTPException(400, f"kind must be one of {ENDPOINT_KINDS} — direct model APIs only")
    key = name or body.name

    def mutate(endpoints: dict) -> None:
        spec = {k: v for k, v in body.model_dump().items()
                if k != "name" and v not in ("", None)}
        # keep a previously-saved inline key when the editor submits the key field blank
        if not spec.get("api_key") and endpoints.get(key, {}).get("api_key"):
            spec["api_key"] = endpoints[key]["api_key"]
        endpoints[key] = spec

    return _rewrite_endpoints(request, mutate)


@router.delete("/settings/endpoints/{name}")
def delete_endpoint(request: Request, name: str) -> dict:
    if name not in _server(request).endpoints:
        raise HTTPException(404, f"no endpoint {name!r}")

    def mutate(endpoints: dict) -> None:
        endpoints.pop(name, None)

    return _rewrite_endpoints(request, mutate)


class TestBody(BaseModel):
    model: str


@router.post("/settings/endpoints/{name}/test")
async def test_endpoint(request: Request, name: str, body: TestBody) -> dict:
    server = _server(request)
    if name not in server.endpoints:
        raise HTTPException(404, f"no endpoint {name!r}")
    ep = EndpointRegistry(server).get(name)

    def call() -> dict:
        start = time.monotonic()
        completion = ep.complete(
            [{"role": "user", "content": "What is 2+3? Reply as one JSON object matching the schema."}],
            model=body.model, schema=TEST_SCHEMA, timeout=90)
        latency = round((time.monotonic() - start) * 1000)
        schema_ok, value = True, None
        try:
            obj = completion.parsed if completion.parsed is not None else parse_reply(
                completion.text, TEST_SCHEMA)
            value = obj.get("answer")
        except SchemaViolation:
            schema_ok = False
        return {"ok": True, "latency_ms": latency, "schema_ok": schema_ok,
                "answer": value, "usage": completion.usage}

    try:
        return await asyncio.to_thread(call)
    except EndpointError as exc:
        return {"ok": False, "error": str(exc), "auth": exc.auth}
