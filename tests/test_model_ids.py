"""The model ids an endpoint itself says it serves — the lists behind the console's model-id
pickers. Read through the reader `limits` already uses, soft on every failure, and never
carrying the endpoint's key out of the server."""

from __future__ import annotations

import json

import httpx
import pytest

from rsched.config import EndpointConfig
from rsched.endpoints import model_ids

SECRET = "proxy-client-secret"


@pytest.fixture
def served(monkeypatch):
    """Answer every `httpx.get` from `handler(url, headers)`, recording what was asked."""
    asked: list[tuple[str, dict]] = []

    def install(reply):
        def fake(url, headers=None, **_kw):
            asked.append((str(url), dict(headers or {})))
            result = reply(str(url)) if callable(reply) else reply
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(httpx, "get", fake)
        return asked
    monkeypatch.setattr(model_ids, "resolve_api_key", lambda **kw: SECRET)
    return install


def _proxy(**kw) -> EndpointConfig:
    return EndpointConfig(name="proxy", kind="anthropic", base_url="http://cliproxy:8317", **kw)


def test_an_anthropic_proxy_is_asked_on_v1_models_with_the_endpoints_own_key(served):
    asked = served(httpx.Response(200, json={"data": [{"id": "gemini-3-pro"},
                                                      {"id": "claude-opus-5"},
                                                      {"id": "gpt-oss-120b-medium"},
                                                      {"object": "model"}]}))
    out = model_ids.list_ids(_proxy())
    assert out == {"ok": True, "ids": ["claude-opus-5", "gemini-3-pro", "gpt-oss-120b-medium"]}
    (url, headers), = asked
    assert url == "http://cliproxy:8317/v1/models"
    assert headers["Authorization"] == f"Bearer {SECRET}" and headers["x-api-key"] == SECRET


def test_an_openai_endpoint_is_asked_on_models_beneath_its_own_v1(served):
    asked = served(httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]}))
    ep = EndpointConfig(name="vllm", kind="openai", base_url="http://host:8000/v1")
    assert model_ids.list_ids(ep) == {"ok": True, "ids": ["a", "b"]}
    assert asked[0][0] == "http://host:8000/v1/models"
    assert "x-api-key" not in asked[0][1]


def test_openrouter_and_nanogpt_use_their_own_catalog_readers(served):
    served(lambda url: httpx.Response(200, json=(
        {"data": [{"id": "z-ai/glm-5.2", "context_length": 100}]} if "openrouter" in url
        else {"models": {"text": {"nano/one": {}, "nano/two": {"maxInputTokens": 5}}}})))
    ep = EndpointConfig(name="or", kind="openai", base_url="https://openrouter.ai/api/v1")
    assert model_ids.list_ids(ep) == {"ok": True, "ids": ["z-ai/glm-5.2"]}
    nano = EndpointConfig(name="nano", kind="openai", base_url="https://nano-gpt.com/api/v1")
    assert model_ids.list_ids(nano) == {"ok": True, "ids": ["nano/one", "nano/two"]}


def test_a_listing_with_no_models_is_ok_and_empty_not_an_error(served):
    served(httpx.Response(200, json={"data": []}))
    assert model_ids.list_ids(_proxy()) == {"ok": True, "ids": []}


@pytest.mark.parametrize("reply", [
    httpx.Response(401, text=SECRET), httpx.Response(500, json={"error": SECRET}),
    httpx.Response(200, text=f"<html>{SECRET}</html>"),
    httpx.Response(200, json={"models": SECRET}), httpx.Response(200, json=[SECRET]),
    httpx.ConnectError(f"refused {SECRET}"),
])
def test_every_failure_is_a_soft_fixed_sentence_that_never_carries_the_key(served, reply):
    served(reply)
    out = model_ids.list_ids(_proxy())
    assert out["ok"] is False and out["error"]
    assert SECRET not in json.dumps(out)


def test_an_endpoint_without_a_base_url_is_told_so_before_any_request(served):
    asked = served(httpx.Response(200, json={"data": []}))
    out = model_ids.list_ids(EndpointConfig(name="bare", kind="anthropic"))
    assert out["ok"] is False and "base URL" in out["error"] and not asked


# ---- the route ---------------------------------------------------------------------------


def test_the_route_is_soft_404s_an_unknown_endpoint_and_refuses_the_routine_token(
        api_client, monkeypatch):
    c, _tmp = api_client
    monkeypatch.setattr(model_ids, "list_ids", lambda ep: {"ok": True, "ids": ["m-1"]})
    assert c.get("/api/settings/endpoints/dummy/model-ids").json() == {"ok": True, "ids": ["m-1"]}
    assert c.get("/api/settings/endpoints/nope/model-ids").status_code == 404
    c.app.state.server.routine_token = "routine-only"
    assert c.get("/api/settings/endpoints/dummy/model-ids",
                 headers={"Authorization": "Bearer routine-only"}).status_code == 403


def test_the_route_reaches_the_network_only_through_the_endpoints_config(api_client, served):
    c, _tmp = api_client
    r = c.post("/api/settings/endpoints", json={"name": "gw", "kind": "anthropic",
                                                "base_url": "http://gw:8317"})
    assert r.status_code == 200, r.text
    asked = served(httpx.Response(200, json={"data": [{"id": "gemini-3-flash"}]}))
    assert c.get("/api/settings/endpoints/gw/model-ids").json() == {
        "ok": True, "ids": ["gemini-3-flash"]}
    assert asked[0][0] == "http://gw:8317/v1/models"
