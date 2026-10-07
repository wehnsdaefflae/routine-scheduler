"""Decision-endpoint settings: CRUD over config.yaml's `decision_endpoints` / `decision_models`
blocks, the two defaults, and a live yes/no probe (docs/decision-models.md).

The decision catalog is its own block beside `endpoints:`/`models:` — a decision model returns
probabilities, never text, so no chat role may resolve into one (config/decisionconf.py). These
routes mirror the chat ones in shape so the console can render both the same way.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...config.decisionconf import (
    DECISION_MEDIA_PROTOCOLS,
    DECISION_PROTOCOLS,
    DEFAULT_DECISION_TIMEOUT_S,
    DecisionEndpointConfig,
    DecisionModelConfig,
    multimodal_effective,
)
from ...endpoints.base import EndpointError, api_key_source
from ...endpoints.decisions import (
    DEFAULT_BASE_URLS,
    DecisionQuestion,
    decide,
    resolve_decision,
)
from .common import reload_into, rewrite_block, server_of, update_config

router = APIRouter()

#: Every live field a decision save can change — mirrored onto the running ServerConfig.
MIRROR = ("decision_endpoints", "decision_models", "decision_model", "decision_media_model")
#: The probe: a question with one obvious answer, so a working model shows P(yes) near 1.
PROBE_EVIDENCE = "Water at sea level boils at 100 degrees Celsius."
PROBE = DecisionQuestion("probe", "yes_no",
                         "Does water at sea level boil at 100 degrees Celsius?")


def _endpoint_view(name: str, ep: DecisionEndpointConfig) -> dict:
    src = api_key_source(api_key=ep.api_key, key_var=ep.key_var, key_env_file=ep.key_env_file)
    if src["source"] == "none" and ep.protocol == "openai":
        src["keyless_ok"] = True   # a self-hosted server on a trusted network may need none
    return {"name": name, "protocol": ep.protocol, "base_url": ep.base_url,
            "default_base_url": DEFAULT_BASE_URLS[ep.protocol], "key_var": ep.key_var,
            "key_env_file": ep.key_env_file, "timeout_s": ep.timeout_s,
            "images": ep.protocol in DECISION_MEDIA_PROTOCOLS,
            "has_inline_key": bool(ep.api_key), "key_source": src}


def _model_view(mc: DecisionModelConfig, server) -> dict:
    ep = server.decision_endpoints.get(mc.endpoint)
    return {"name": mc.name, "endpoint": mc.endpoint, "model": mc.model,
            "multimodal": mc.multimodal, "images": multimodal_effective(mc, ep),
            "protocol": ep.protocol if ep else None}


@router.get("/settings/decisions")
def list_decisions(request: Request) -> dict:
    """The whole decision catalog: endpoints, models, and the two defaults."""
    server = server_of(request)
    return {"protocols": list(DECISION_PROTOCOLS),
            "endpoints": [_endpoint_view(n, e) for n, e in server.decision_endpoints.items()],
            "models": [_model_view(m, server) for m in server.decision_models.values()],
            "decision_model": server.decision_model or None,
            "decision_media_model": server.decision_media_model or None}


def _rewrite(request: Request, key: str, mutate: Callable[[dict], None]) -> dict:
    return rewrite_block(request, key, mutate, *MIRROR)


class DecisionEndpointBody(BaseModel):
    """A decision endpoint as the console saves it; a blank field keeps what was saved."""

    name: str = Field(min_length=1)
    protocol: str
    base_url: str = ""
    api_key: str = ""
    key_env_file: str = ""
    key_var: str = ""
    timeout_s: int = Field(DEFAULT_DECISION_TIMEOUT_S, ge=5, le=3600)


@router.post("/settings/decision-endpoints")
@router.put("/settings/decision-endpoints/{name}")
def upsert_decision_endpoint(request: Request, body: DecisionEndpointBody,
                             name: str | None = None) -> dict:
    """Create or replace one decision endpoint. A blank key keeps the saved one."""
    if body.protocol not in DECISION_PROTOCOLS:
        raise HTTPException(400, f"protocol must be one of {list(DECISION_PROTOCOLS)}")
    key = name or body.name.strip()

    def mutate(block: dict) -> None:
        prev = block.get(key, {})
        spec = {k: v for k, v in body.model_dump().items() if k != "name" and v not in ("", None)}
        if not spec.get("api_key") and prev.get("api_key"):
            spec["api_key"] = prev["api_key"]
        if "key_env_file" not in spec and prev.get("key_env_file"):
            spec["key_env_file"] = prev["key_env_file"]
        block[key] = spec

    return _rewrite(request, "decision_endpoints", mutate)


@router.delete("/settings/decision-endpoints/{name}")
def delete_decision_endpoint(request: Request, name: str) -> dict:
    """Remove a decision endpoint no decision model uses."""
    server = server_of(request)
    if name not in server.decision_endpoints:
        raise HTTPException(404, f"no decision endpoint {name!r}")
    used = [m.name for m in server.decision_models.values() if m.endpoint == name]
    if used:
        raise HTTPException(400, f"{name!r} still serves decision model(s) {used} — reassign "
                                 "or delete them first")
    return _rewrite(request, "decision_endpoints", lambda block: block.pop(name, None))


class DecisionModelBody(BaseModel):
    """A decision model as the console saves it."""

    name: str = Field(min_length=1)
    endpoint: str
    model: str = Field(min_length=1)
    multimodal: bool | None = None   # None = the protocol's default


@router.post("/settings/decision-models")
@router.put("/settings/decision-models/{name}")
def upsert_decision_model(request: Request, body: DecisionModelBody,
                          name: str | None = None) -> dict:
    """Create or replace one decision model, bound to a configured decision endpoint."""
    server = server_of(request)
    ep = server.decision_endpoints.get(body.endpoint)
    if ep is None:
        raise HTTPException(400, f"unknown decision endpoint {body.endpoint!r} — add it first")
    if body.multimodal and ep.protocol not in DECISION_MEDIA_PROTOCOLS:
        raise HTTPException(400, f"the {ep.protocol} protocol carries text only — a model on "
                                 f"{body.endpoint!r} cannot take images")
    key = name or body.name.strip()

    def mutate(block: dict) -> None:
        block[key] = {k: v for k, v in body.model_dump().items()
                      if k != "name" and v not in ("", None)}

    return _rewrite(request, "decision_models", mutate)


@router.delete("/settings/decision-models/{name}")
def delete_decision_model(request: Request, name: str) -> dict:
    """Remove a decision model that is neither default."""
    server = server_of(request)
    if name not in server.decision_models:
        raise HTTPException(404, f"no decision model {name!r}")
    if name in (server.decision_model, server.decision_media_model):
        raise HTTPException(400, f"{name!r} is a default decision model — choose another "
                                 "default first")
    return _rewrite(request, "decision_models", lambda block: block.pop(name, None))


class DefaultsBody(BaseModel):
    """The two defaults; a blank one clears it."""

    decision_model: str = ""
    decision_media_model: str = ""


@router.put("/settings/decision-defaults")
def set_decision_defaults(request: Request, body: DefaultsBody) -> dict:
    """Set the default decision model and the default for a call that carries images."""
    server = server_of(request)
    for value in (body.decision_model, body.decision_media_model):
        if value and value not in server.decision_models:
            raise HTTPException(400, f"unknown decision model {value!r}")
    media = server.decision_models.get(body.decision_media_model)
    if media is not None and not multimodal_effective(
            media, server.decision_endpoints.get(media.endpoint)):
        raise HTTPException(400, f"{body.decision_media_model!r} does not take images")

    def apply(raw: dict) -> None:
        raw["decision_model"] = body.decision_model
        raw["decision_media_model"] = body.decision_media_model

    path = update_config(request, apply)
    return {"ok": True, "problems": reload_into(request, path, *MIRROR)}


@router.post("/settings/decision-models/{name}/test")
async def test_decision_model(request: Request, name: str) -> dict:
    """One live yes/no question with an obvious answer: latency, P(yes) and usage back."""
    server = server_of(request)
    if name not in server.decision_models:
        raise HTTPException(404, f"no decision model {name!r}")

    def call() -> dict:
        endpoint, ref = resolve_decision(server, name)
        start = time.monotonic()
        result = decide(endpoint, ref, PROBE_EVIDENCE, [], [PROBE], timeout=90,
                        purpose=f"Test decision model {name}")
        answer = result.answers[0]
        return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000),
                "probability": answer.probability, "refused": answer.refused,
                "served_by": result.model or ref.model, "usage": result.usage}

    try:
        return await asyncio.to_thread(call)
    except EndpointError as exc:
        return {"ok": False, "error": str(exc), "auth": exc.auth}
