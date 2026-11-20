"""A gateway model is sized by its ROUTE, never by the model's public figure.

An Otari gateway serves normalised names and its `aliases:` send each to a provider: a
CLIProxyAPI subscription login, OpenRouter, Nano-GPT, Anthropic's own API. The window and output
maximum belong to that route — the Codex Plus login serves `gpt-5.6-luna` at 372,000 tokens where
the public catalog says 1,050,000 — so discovery reads the gateway's routing file, takes each
route's figures from that route's own catalog, and leaves a route that publishes none UNKNOWN.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from helpers import tmp_server
from rsched.config import EndpointConfig, GatewayConfig, ModelConfig, load_server_config
from rsched.endpoints import EndpointRegistry, catalogs, limits, routecatalogs, routes

ROUTES = {
    "providers": {
        "openrouter": {"api_key": "${OPENROUTER_API_KEY}"},
        "anthropic": {"api_key": "${ANTHROPIC_API_KEY}"},
        "cliproxy": {"provider_type": "anthropic", "api_base": "http://cache-marker:8317"},
        "chatgpt": {"provider_type": "openai", "api_base": "http://cliproxy:8317/v1"},
        "nanogpt": {"provider_type": "openai", "api_base": "https://nano-gpt.com/api/v1"},
        "local-qwen": {"provider_type": "openai", "api_base": "http://decision-balancer:8790/v1"},
    },
    "aliases": {
        "claude-opus-5": "cliproxy:claude-opus-5",
        "gemini-4-argon": "cliproxy:gemini-4-argon-high",
        "gpt-5.6-luna": "chatgpt:gpt-5.6-luna",
        "gpt-6.2-sol-max": "chatgpt:gpt-6.2-sol(max)",
        "claude-opus-5-5-metered": "anthropic:claude-opus-5-5",
        "claude-opus-5-metered": "openrouter:anthropic/claude-opus-5",
        "glm-5.3": "openrouter:z-ai/glm-5.3",
        "glm-5.3-uncensored": "nanogpt:z-ai/glm-5.3-uncensored",
        "mimo-v2.6-pro": "openrouter:xiaomi/mimo-v2.6-pro",
        "qwen3-vl-8b-instruct": "local-qwen:qwen3-vl-8b-instruct",
    },
    "routing": {"policies": {"tagfs-chat": {"select": [{"default": "chatgpt:gpt-5.6-luna"}]}}},
}
PLANS = {"cliproxy": ["claude", "antigravity"], "chatgpt": ["codex-plus"]}
CLIPROXY = {
    "claude": [{"id": "claude-opus-5", "context_length": 1_000_000,
                "max_completion_tokens": 128_000}],
    "antigravity": [{"id": "gemini-4-argon-high", "inputTokenLimit": 1_048_576,
                     "outputTokenLimit": 65_536}],
    "codex-plus": [{"id": "gpt-5.6-luna", "context_length": 372_000,
                    "max_completion_tokens": 128_000},
                   {"id": "gpt-6.2-sol", "context_length": 500_000,
                    "max_completion_tokens": 128_000}],
    "codex-pro": [{"id": "gpt-5.6-luna", "context_length": 1_050_000,
                   "max_completion_tokens": 128_000}],
}
OPENROUTER = {"data": [
    {"id": "anthropic/claude-opus-5", "context_length": 1_000_000,
     "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 128_000}},
    {"id": "openai/gpt-5.6-luna", "context_length": 1_050_000,
     "top_provider": {"context_length": 1_050_000, "max_completion_tokens": 128_000}},
    {"id": "z-ai/glm-5.3", "context_length": 1_048_576,
     "top_provider": {"context_length": 1_048_576, "max_completion_tokens": 943_718}},
    {"id": "xiaomi/mimo-v2.6-pro", "context_length": 1_050_000,
     "top_provider": {"context_length": 1_048_576, "max_completion_tokens": 131_072}},
]}
NANOGPT = {"data": [
    {"id": "z-ai/glm-5.3", "context_length": 1_048_576, "max_output_tokens": 131_072},
    {"id": "z-ai/glm-5.3-uncensored", "context_length": 1_048_576, "max_output_tokens": 131_072},
]}
GATEWAY_LISTING = {"data": [{"id": name} for name in ROUTES["aliases"]]}


def _answer(monkeypatch, *, cliproxy=CLIPROXY, openrouter=OPENROUTER, nanogpt=NANOGPT):
    seen: list[str] = []

    def get(url, headers=None):
        seen.append(url)
        if url == routecatalogs.CLIPROXY_CATALOG_URL:
            return cliproxy
        if url == catalogs.REFERENCE_URL:
            return openrouter
        if url.startswith(catalogs.NANOGPT_ORIGIN):
            return nanogpt
        return GATEWAY_LISTING

    monkeypatch.setattr(catalogs, "get_json", get)
    return seen


def _gateway(tmp_path, *models, doc=ROUTES, plans=PLANS):
    routes_file = tmp_path / "otari-config.yml"
    routes_file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_server(
        tmp_path,
        gateways={"otari": GatewayConfig(name="otari", routes_file=str(routes_file), plans=plans)},
        endpoints={"otari": EndpointConfig(name="otari", kind="anthropic", gateway="otari",
                                           base_url="http://172.17.0.1:4000/api",
                                           context_tokens=200_000)},
        models={m: ModelConfig(name=m, endpoint="otari", model=m) for m in models})


def _row(server, model):
    return limits.lookup(server.routines_home, "otari", model)


# ---- where a name goes -----------------------------------------------------------------------

def _gw(plans=PLANS):
    return GatewayConfig(name="otari", routes_file="x", plans=plans)


@pytest.mark.parametrize(("name", "provider", "upstream", "catalog"), [
    ("gpt-5.6-luna", "chatgpt", "gpt-5.6-luna", "cliproxy"),
    ("gemini-4-argon", "cliproxy", "gemini-4-argon-high", "cliproxy"),
    ("glm-5.3", "openrouter", "z-ai/glm-5.3", "openrouter"),
    ("glm-5.3-uncensored", "nanogpt", "z-ai/glm-5.3-uncensored", "nanogpt"),
    ("claude-opus-5-5-metered", "anthropic", "claude-opus-5-5", "anthropic"),
    ("openrouter:z-ai/glm-5.3", "openrouter", "z-ai/glm-5.3", "openrouter"),  # a selector
    ("qwen3-vl-8b-instruct", "local-qwen", "qwen3-vl-8b-instruct", ""),         # nothing to read
])
def test_a_name_resolves_to_its_route(name, provider, upstream, catalog):
    route = routes.resolve(_gw(), ROUTES, name)
    assert (route.provider, route.upstream, route.catalog) == (provider, upstream, catalog)


@pytest.mark.parametrize("name", ["tagfs-chat", "never-heard-of-it", "nowhere:some-model"])
def test_a_name_with_no_single_route_resolves_to_nothing(name):
    """A routing POLICY may serve several models in turn; an unknown name or provider, none."""
    assert routes.resolve(_gw(), ROUTES, name) is None


def test_the_plan_comes_from_config_and_nowhere_else():
    """Without a plan the proxy-backed provider is an anthropic-typed host like any other: its
    catalog cannot be named, so it is not guessed."""
    assert routes.resolve(_gw({}), ROUTES, "claude-opus-5").catalog == ""
    assert routes.resolve(_gw(), ROUTES, "claude-opus-5").plans == ("claude", "antigravity")


def test_a_level_pin_is_not_part_of_the_catalog_id():
    assert routes.resolve(_gw(), ROUTES, "gpt-6.2-sol-max").catalog_id() == "gpt-6.2-sol"


# ---- the figures -------------------------------------------------------------------------------

def test_every_route_is_sized_from_its_own_catalog(tmp_path, monkeypatch):
    server = _gateway(tmp_path, *ROUTES["aliases"])
    _answer(monkeypatch)
    out = limits.refresh(server, force=True)
    got = {m: (r["context_tokens"], r["max_output_tokens"], r["source"])
           for m in ROUTES["aliases"] if (r := _row(server, m))}
    assert got == {
        # the Codex Plus plan, not the public 1,050,000
        "gpt-5.6-luna": (372_000, 128_000, "cliproxy"),
        "gpt-6.2-sol-max": (500_000, 128_000, "cliproxy"),
        "claude-opus-5": (1_000_000, 128_000, "cliproxy"),
        "gemini-4-argon": (1_048_576, 65_536, "cliproxy"),        # Gemini's own row shape
        # OpenRouter's 943,718 is its 0.9 placeholder: Nano-GPT's real figure replaces it
        "glm-5.3": (1_048_576, 131_072, "openrouter"),
        "glm-5.3-uncensored": (1_048_576, 131_072, "nanogpt"),
        "mimo-v2.6-pro": (1_048_576, 131_072, "openrouter"),     # top_provider, not the headline
        # a `-metered` name is its paid ROUTE's, like any other name
        "claude-opus-5-metered": (1_000_000, 128_000, "openrouter"),
        # Anthropic's own API: the table's window, and no output figure nobody published
        "claude-opus-5-5-metered": (1_000_000, None, "table"),
    }
    assert out["misses"] == ["otari/qwen3-vl-8b-instruct"]
    assert _row(server, "glm-5.3")["provider_max_output_tokens"] == 943_718
    assert _row(server, "gpt-6.2-sol-max")["route"] == "chatgpt:gpt-6.2-sol(max) [codex-plus]"
    _ep, ref = EndpointRegistry(server).resolve("gpt-5.6-luna")
    assert (ref.context_tokens, ref.max_tokens) == (372_000, 128_000)


def test_each_catalog_is_read_once_per_refresh(tmp_path, monkeypatch):
    server = _gateway(tmp_path, *ROUTES["aliases"])
    seen = _answer(monkeypatch)
    limits.refresh(server, force=True)
    assert seen.count(routecatalogs.CLIPROXY_CATALOG_URL) == 1
    assert seen.count(catalogs.REFERENCE_URL) == 1        # the OpenRouter route AND the prices


def test_plans_that_disagree_leave_the_route_unknown(tmp_path, monkeypatch):
    """Which login serves a call is the proxy's choice: two plans, two windows, no answer."""
    server = _gateway(tmp_path, "gpt-5.6-luna", plans={"chatgpt": ["codex-plus", "codex-pro"]})
    _answer(monkeypatch)
    assert limits.refresh(server, force=True)["misses"] == ["otari/gpt-5.6-luna"]


def test_a_placeholder_with_no_agreeing_source_leaves_the_output_unknown(tmp_path, monkeypatch):
    server = _gateway(tmp_path, "glm-5.3")
    _answer(monkeypatch, nanogpt={"data": [
        {"id": "z-ai/glm-5.3", "context_length": 1_048_576, "max_output_tokens": 131_072},
        {"id": "TEE/glm-5.3", "context_length": 1_048_576, "max_output_tokens": 262_144}]})
    limits.refresh(server, force=True)
    row = _row(server, "glm-5.3")
    assert row["context_tokens"] == 1_048_576 and row["max_output_tokens"] is None


def test_a_route_its_catalog_does_not_list_is_never_given_the_public_figure(tmp_path, monkeypatch):
    server = _gateway(tmp_path, "gpt-5.6-luna")
    _answer(monkeypatch, cliproxy={})
    assert limits.refresh(server, force=True)["misses"] == ["otari/gpt-5.6-luna"]
    assert _row(server, "gpt-5.6-luna") is None


def test_unreadable_routes_keep_what_was_known(tmp_path, monkeypatch):
    server = _gateway(tmp_path, "gpt-5.6-luna")
    _answer(monkeypatch)
    limits.refresh(server, force=True)
    (tmp_path / "otari-config.yml").unlink()
    assert limits.refresh(server, force=True)["misses"] == ["otari/gpt-5.6-luna"]
    assert _row(server, "gpt-5.6-luna")["context_tokens"] == 372_000


def test_an_edited_routing_file_makes_the_cache_stale(tmp_path, monkeypatch):
    server = _gateway(tmp_path, "gpt-5.6-luna")
    _answer(monkeypatch)
    limits.refresh(server, force=True)
    monkeypatch.setattr(limits.prices, "missing", lambda _server: False)
    assert limits.stale(server) is False
    later = (datetime.now(UTC) + timedelta(minutes=5)).timestamp()
    os.utime(tmp_path / "otari-config.yml", (later, later))
    assert limits.stale(server) is True


@pytest.mark.parametrize(("figures", "placeholder"), [
    ((1_048_576, 943_718), True), ((1_048_575, 943_717), True), ((1_050_000, 945_000), True),
    ((500_000, 500_000), True),                      # the window itself
    ((1_048_576, 131_072), False), ((1_310_720, 943_718), False), ((200_000, None), False),
])
def test_the_placeholder_shapes(figures, placeholder):
    assert routecatalogs.is_placeholder(figures) is placeholder


@pytest.mark.parametrize(("model_id", "name"), [
    ("gemini-4-argon-high", "gemini-4-argon"),
    ("google/gemini-4-argon", "gemini-4-argon"),
    ("google/gemini-4-argon:batch", "gemini-4-argon"),
    ("GPT-OSS-120B-Medium", "gpt-oss-120b"),
    ("claude-opus-4-6-thinking", "claude-opus-4-6"),
    ("gemini-3.8-flash", "gemini-3.8-flash"),            # no level: left alone
    ("glm-5.3-uncensored", "glm-5.3-uncensored"),        # a word that is not a level: left alone
])
def test_ids_reduce_to_the_name_vendors_share(model_id, name):
    """The name a placeholder's real figure is looked up under in the other catalogs."""
    assert catalogs.reference_name(model_id) == name


# ---- config ------------------------------------------------------------------------------------

def test_the_config_names_gateways_and_flags_an_unknown_one(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "gateways": {"otari": {"routes_file": "/srv/llm-interfaces/config.yml",
                               "plans": {"chatgpt": ["codex-plus"]}}},
        "endpoints": {"a": {"kind": "openai", "gateway": "otari"},
                      "b": {"kind": "openai", "gateway": "elsewhere"}}}), encoding="utf-8")
    cfg, problems = load_server_config(path)
    assert cfg.gateways["otari"].name == "otari"
    assert cfg.gateways["otari"].plans == {"chatgpt": ["codex-plus"]}
    assert cfg.endpoints["a"].gateway == "otari"
    assert problems == ["endpoints.b: gateway 'elsewhere' is not configured"]
