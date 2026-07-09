"""Endpoint settings: CRUD over the config.yaml endpoints block + a live test call."""

from __future__ import annotations

import asyncio
import subprocess
import time

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
