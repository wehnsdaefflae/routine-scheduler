"""Asking an endpoint about ITSELF — the credits read, the subscription quota, and the live
connection test.

Split out of `endpoints.py` (F393): storing endpoint config and going out to the network to see
whether it works are different jobs, and only this one can be slow, fail, or cost money. Keeping
them apart means a settings save is never held up by a provider that is down.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...endpoints import EndpointRegistry, cliproxy_login, cliproxy_mgmt, cliproxy_quota
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

def _endpoint(request: Request, name: str):
    ep = server_of(request).endpoints.get(name)
    if ep is None:
        raise HTTPException(404, f"no endpoint {name!r}")
    return ep


@router.get("/settings/endpoints/{name}/quota")
async def endpoint_quota(request: Request, name: str) -> dict:
    """What is LEFT of the Claude subscription's rolling windows — the operator's literal ask.

    A sibling of the credits read above and deliberately shaped like it: `{"supported": false}`
    for endpoints without a quota source, never raises, and the card renders the error text
    when the provider or the credential is the problem. The quota is per-ACCOUNT rather than per
    endpoint, so two subscription proxy endpoints would honestly report the same numbers.
    """
    server = server_of(request)
    _endpoint(request, name)
    bound = cliproxy_mgmt.binding(server, name)
    providers = cliproxy_mgmt.endpoint_providers(server, name)
    # the Claude subscription's windows, read through whichever endpoint carries the
    # binding — but only ON a card whose models are Claude's (unknown families count as
    # Claude's, since the read was configured somewhere); the Codex card gets none
    if bound is None or (providers and "claude" not in providers):
        return {"supported": False}
    return await asyncio.to_thread(cliproxy_quota.read_quota, bound)


# ---- signing the proxy's accounts back in, from the card -----------------------------------
# The management binding is the proxy's (`quota_source: cliproxy` + the management key in
# `quota_key_var` on ANY endpoint of that proxy — cliproxy_mgmt.binding resolves a sibling's),
# and which accounts a card shows follows the provider of the models bound to that endpoint.
# The proxy owns the OAuth flow; these three routes ferry the consent URL out to the
# operator's browser and the code back (endpoints/cliproxy_login.py explains why the code
# comes back by hand: the consent page redirects to localhost on the OPERATOR'S device).
# Mutating routes, so the read-only routine token is refused like every other POST.

def _proxy_binding(request: Request, name: str):
    _endpoint(request, name)
    bound = cliproxy_mgmt.binding(server_of(request), name)
    if bound is None:
        raise HTTPException(400, f"endpoint {name!r} has no proxy management binding — set "
                                 "Proxy management to CLIProxyAPI on it (or on any endpoint "
                                 "of the same proxy) first")
    return bound


@router.get("/settings/endpoints/{name}/proxy-accounts")
async def proxy_accounts(request: Request, name: str) -> dict:
    """Who the proxy is signed in as, for THIS endpoint's models — provider, label, the
    proxy's status word and its one-line reason when an account is unavailable (a dead
    refresh token, a usage limit) — plus the sign-in controls the card offers.
    `{"supported": false}` for an endpoint that resolves no binding, like the quota read.
    """
    server = server_of(request)
    _endpoint(request, name)
    bound = cliproxy_mgmt.binding(server, name)
    if bound is None:
        return {"supported": False}
    return await asyncio.to_thread(cliproxy_login.accounts, bound,
                                   cliproxy_mgmt.endpoint_providers(server, name))


class ProxyLoginStart(BaseModel):
    provider: str


@router.post("/settings/endpoints/{name}/proxy-login")
async def proxy_login_start(request: Request, name: str, body: ProxyLoginStart) -> dict:
    """A consent URL + the state naming this sign-in; the card shows the link."""
    bound = _proxy_binding(request, name)
    return await asyncio.to_thread(cliproxy_login.start, bound, body.provider)


class ProxyLoginComplete(BaseModel):
    provider: str
    state: str
    response: str   # the callback address the operator landed on, `code#state`, or the code


@router.post("/settings/endpoints/{name}/proxy-login/complete")
async def proxy_login_complete(request: Request, name: str,
                               body: ProxyLoginComplete) -> dict:
    """Hand the pasted code to the proxy and wait for its token exchange to settle."""
    bound = _proxy_binding(request, name)
    return await asyncio.to_thread(cliproxy_login.complete, bound, body.provider, body.state,
                                   body.response)


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
