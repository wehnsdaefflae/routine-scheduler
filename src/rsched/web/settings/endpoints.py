"""Endpoint settings: CRUD over the config.yaml endpoints block, the system model,
and a live test call.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...config import (
    ENDPOINT_KINDS,
    NATIVE_MM_KINDS,
    EndpointConfig,
    ModelConfig,
    load_server_config,
)
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


def _model_view(mc: ModelConfig, endpoints: dict) -> dict:
    """A catalog model's raw config PLUS the effective multimodal/context (endpoint-kind or
    endpoint default filled in) so the list can label it and the editor can show what's set.
    """
    ep = endpoints.get(mc.endpoint)
    kind = ep.kind if ep else ""
    return {"name": mc.name, "endpoint": mc.endpoint, "model": mc.model,
            "multimodal": mc.multimodal, "effort": mc.effort, "temperature": mc.temperature,
            "context_chars": mc.context_chars,
            "multimodal_effective": mc.multimodal if mc.multimodal is not None
            else (kind in NATIVE_MM_KINDS),
            "context_effective": mc.context_chars or (ep.context_chars if ep else 0)}


@router.get("/settings/endpoints")
def list_endpoints(request: Request) -> dict:
    server = server_of(request)
    return {"endpoints": [_endpoint_view(n, e) for n, e in server.endpoints.items()],
            "models": [_model_view(m, server.endpoints) for m in server.models.values()],
            "system_model": server.system_model or None}


@router.get("/settings/models")
def list_models(request: Request) -> dict:
    """The model catalog alone — for the routine/conversation model pickers (name → attrs)."""
    server = server_of(request)
    return {"models": [_model_view(m, server.endpoints) for m in server.models.values()],
            "system_model": server.system_model or None}


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
    context_chars: int = 100_000     # a DEFAULT catalog models inherit (per-model window wins)
    temperature: float | None = None  # a DEFAULT catalog models inherit


@router.post("/settings/endpoints")
@router.put("/settings/endpoints/{name}")
def upsert_endpoint(request: Request, body: EndpointBody, name: str | None = None) -> dict:
    if body.kind not in ENDPOINT_KINDS:
        raise HTTPException(400, f"kind must be one of {ENDPOINT_KINDS} — direct model APIs only")
    key = name or body.name

    def mutate(endpoints: dict) -> None:
        spec = {k: v for k, v in body.model_dump().items()
                if k != "name" and v not in ("", None)}
        prev = endpoints.get(key, {})
        # keep a previously-saved inline key when the editor submits the key field blank
        if not spec.get("api_key") and prev.get("api_key"):
            spec["api_key"] = prev["api_key"]
        # A PUT is a full replace, but the credential-save form sends only a subset — preserve
        # config-only / omitted fields (temperature, extra_body) so saving a key never silently
        # drops them.
        for field in ("temperature", "extra_body"):
            if field not in spec and field in prev:
                spec[field] = prev[field]
        endpoints[key] = spec

    return _rewrite_endpoints(request, mutate)


@router.delete("/settings/endpoints/{name}")
def delete_endpoint(request: Request, name: str) -> dict:
    server = server_of(request)
    if name not in server.endpoints:
        raise HTTPException(404, f"no endpoint {name!r}")
    used = [m.name for m in server.models.values() if m.endpoint == name]
    if used:
        raise HTTPException(400, f"{name!r} still serves catalog model(s) {used} — reassign or "
                                 "delete them first")

    def mutate(endpoints: dict) -> None:
        endpoints.pop(name, None)

    return _rewrite_endpoints(request, mutate)


# --- the model catalog: named models bound to an endpoint, carrying per-model attrs ----------
def _rewrite_models(request: Request, mutate) -> dict:
    def apply(raw: dict) -> None:
        models = raw.get("models") or {}
        mutate(models)
        raw["models"] = models

    path = update_config(request, apply)
    fresh, problems = load_server_config(path)
    server = server_of(request)
    server.models = fresh.models
    return {"ok": True, "problems": problems}


class ModelBody(BaseModel):
    name: str
    endpoint: str
    model: str
    multimodal: bool | None = None    # None = default by the endpoint kind
    context_chars: int | None = None  # None = inherit the endpoint's context_chars
    effort: str | None = None
    temperature: float | None = None  # None = inherit the endpoint's temperature


@router.post("/settings/models")
@router.put("/settings/models/{name}")
def upsert_model(request: Request, body: ModelBody, name: str | None = None) -> dict:
    if body.endpoint not in server_of(request).endpoints:
        raise HTTPException(400, f"unknown endpoint {body.endpoint!r} — add it first")
    key = name or body.name

    def mutate(models: dict) -> None:
        # drop empty/None so an unset attribute means "inherit the endpoint default"
        models[key] = {k: v for k, v in body.model_dump().items()
                       if k != "name" and v not in ("", None)}

    return _rewrite_models(request, mutate)


@router.delete("/settings/models/{name}")
def delete_model(request: Request, name: str) -> dict:
    server = server_of(request)
    if name not in server.models:
        raise HTTPException(404, f"no model {name!r}")
    if server.system_model == name:
        raise HTTPException(400, f"{name!r} is the system model — reassign the system model first")

    def mutate(models: dict) -> None:
        models.pop(name, None)

    return _rewrite_models(request, mutate)


class SystemModelBody(BaseModel):
    name: str   # a catalog model name


@router.put("/settings/system-model")
def set_system_model(request: Request, body: SystemModelBody) -> dict:
    """Set the ONE fallback model for machine work that isn't a routine yet — workflow
    generation/suggestion and the new-routine clarify wizard. Setting it is what makes the
    instance 'llm_ready'; routines otherwise pick their own (also by catalog name).
    """
    s = server_of(request)
    if body.name not in s.models:
        raise HTTPException(400, f"unknown model {body.name!r} — add it to the catalog first")
    path = update_config(request, lambda raw: raw.update(system_model=body.name))
    fresh, _ = load_server_config(path)
    s.system_model = fresh.system_model
    return {"ok": True, "system_model": s.system_model or None}


# Providers whose account balance the endpoint card can show, sniffed from base_url.
CREDIT_MANAGE_URLS = {
    "openrouter": "https://openrouter.ai/settings/credits",
    "nanogpt": "https://nano-gpt.com/balance",
}


def credits_provider(ep) -> str | None:
    """Which balance API an endpoint speaks, from its base_url (None = no balance API)."""
    if ep.kind != "openai":
        return None
    base = ep.base_url or ""
    if "openrouter" in base:
        return "openrouter"
    if "nano-gpt.com" in base:
        return "nanogpt"
    return None


def nanogpt_balance_url(base_url: str) -> str:
    """Nano-GPT's check-balance lives at /api/check-balance on the ORIGIN — beside,
    not under, the OpenAI-compatible /api/v1 the endpoint is configured with.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}/api/check-balance"


@router.get("/settings/endpoints/{name}/credits")
async def endpoint_credits(request: Request, name: str) -> dict:
    """Provider account balance, where the provider exposes one (OpenRouter, Nano-GPT):
    remaining $ (plus purchased/used where the API reports them). Never raises on provider
    trouble — the card shows the error text instead.
    """
    server = server_of(request)
    ep = server.endpoints.get(name)
    if ep is None:
        raise HTTPException(404, f"no endpoint {name!r}")
    provider = credits_provider(ep)
    if provider is None:
        return {"supported": False}
    manage = CREDIT_MANAGE_URLS[provider]

    def call() -> dict:
        import httpx

        from ...endpoints.openai_compat import OpenAICompatEndpoint

        key = OpenAICompatEndpoint(ep)._resolve_key()
        try:
            if provider == "openrouter":
                resp = httpx.get(f"{ep.base_url.rstrip('/')}/credits",
                                 headers={"Authorization": f"Bearer {key}"}, timeout=15)
            else:   # nanogpt — POST, x-api-key auth (docs.nano-gpt.com check-balance)
                resp = httpx.post(nanogpt_balance_url(ep.base_url),
                                  headers={"x-api-key": key}, timeout=15)
        except httpx.HTTPError as exc:
            return {"supported": True, "ok": False, "error": str(exc), "manage_url": manage}
        if resp.status_code != 200:
            return {"supported": True, "ok": False, "manage_url": manage,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        if provider == "openrouter":
            data = resp.json().get("data") or {}
            total = float(data.get("total_credits") or 0)
            used = float(data.get("total_usage") or 0)
            return {"supported": True, "ok": True, "total": round(total, 4),
                    "used": round(used, 4), "remaining": round(total - used, 4),
                    "manage_url": manage}
        # nanogpt shape: {"usd_balance": "9.91856570", "nano_balance": "..."} — strings
        return {"supported": True, "ok": True, "manage_url": manage,
                "remaining": round(float(resp.json().get("usd_balance") or 0), 4)}

    try:
        return await asyncio.to_thread(call)
    except EndpointError as exc:   # no key configured yet
        return {"supported": True, "ok": False, "error": str(exc), "manage_url": manage}


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
            [{"role": "user",
              "content": "What is 2+3? Reply as one JSON object matching the schema."}],
            model=body.model, schema=TEST_SCHEMA, timeout=90,
            purpose=f"Test endpoint {name}", kind="test")
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
