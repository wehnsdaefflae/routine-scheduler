"""Asking an endpoint about ITSELF — the credits read and the live connection test.

Split out of `endpoints.py` (F393): storing endpoint config and going out to the network to see
whether it works are different jobs, and only this one can be slow, fail, or cost money. Keeping
them apart means a settings save is never held up by a provider that is down.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...endpoints import EndpointRegistry
from ...endpoints.base import EndpointError
from ...schema_guard import SchemaViolation, parse_reply
from .common import server_of
from .endpoints import CREDIT_MANAGE_URLS, TEST_SCHEMA

router = APIRouter(tags=["endpoint-probe"])


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
