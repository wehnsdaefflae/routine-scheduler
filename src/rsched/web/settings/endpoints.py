"""Endpoint settings: CRUD over the config.yaml endpoints block, the system model,
and a live test call."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...config import ENDPOINT_KINDS, EndpointConfig, load_server_config
from ...endpoints import EndpointRegistry
from ...endpoints.base import EndpointError
from ...schema_guard import SchemaViolation, parse_reply
from .common import server_of, update_config

router = APIRouter()

TEST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"],
               "properties": {"answer": {"type": "integer"}}}


def _endpoint_view(name: str, ep: EndpointConfig) -> dict:
    return {"name": name, "kind": ep.kind, "base_url": ep.base_url,
            "key_env_file": ep.key_env_file, "key_var": ep.key_var,
            "schema_mode": ep.schema_mode, "context_chars": ep.context_chars,
            "temperature": ep.temperature, "has_inline_key": bool(ep.api_key)}


@router.get("/settings/endpoints")
def list_endpoints(request: Request) -> dict:
    server = server_of(request)
    sm = server.system_model
    return {"endpoints": [_endpoint_view(n, e) for n, e in server.endpoints.items()],
            "system_model": {"endpoint": sm.endpoint, "model": sm.model} if sm else None}


def _rewrite_endpoints(request: Request, mutate) -> dict:
    def apply(raw: dict) -> None:
        endpoints = raw.get("endpoints") or {}
        mutate(endpoints)
        raw["endpoints"] = endpoints

    path = update_config(request, apply)
    fresh, problems = load_server_config(path)
    server = server_of(request)
    server.endpoints = fresh.endpoints
    server.system_model = fresh.system_model
    return {"ok": True, "problems": problems}


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
    if name not in server_of(request).endpoints:
        raise HTTPException(404, f"no endpoint {name!r}")

    def mutate(endpoints: dict) -> None:
        endpoints.pop(name, None)

    return _rewrite_endpoints(request, mutate)


class SystemModelBody(BaseModel):
    endpoint: str
    model: str
    effort: str | None = None


@router.put("/settings/system-model")
def set_system_model(request: Request, body: SystemModelBody) -> dict:
    """Set the ONE fallback model for machine work that isn't a routine yet — workflow
    generation/suggestion and the new-routine clarify wizard. Setting it is what makes the
    instance 'llm_ready'; routines otherwise pick their own models."""
    s = server_of(request)
    if body.endpoint not in s.endpoints:
        raise HTTPException(400, f"unknown endpoint {body.endpoint!r} — add it first")
    spec = {"endpoint": body.endpoint, "model": body.model}
    if body.effort:
        spec["effort"] = body.effort
    path = update_config(request, lambda raw: raw.update(system_model=spec))
    fresh, _ = load_server_config(path)
    s.system_model = fresh.system_model
    sm = s.system_model
    return {"ok": True, "system_model": {"endpoint": sm.endpoint, "model": sm.model} if sm else None}


class TestBody(BaseModel):
    model: str


@router.post("/settings/endpoints/{name}/test")
async def test_endpoint(request: Request, name: str, body: TestBody) -> dict:
    server = server_of(request)
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
