"""Provider-discovered model limits — the operator's "max out the token window without setting
it up".

The two things worth pinning are the PRECEDENCE (an explicit config value is an operator sizing
down on purpose and must still win) and the asymmetry between the two knobs: the input window is
adopted verbatim, the OUTPUT cap is clamped to what this harness needs. Maxing the output cap is
the obvious misreading of the request and it is actively harmful — providers validate
`input + requested_output <= window`, so a model whose real output limit is 943,718 tokens would
have ~10% of its 1M window left for the prompt.
"""

from __future__ import annotations

import json

import httpx
import pytest

from rsched.config import EndpointConfig, ModelConfig, ServerConfig
from rsched.endpoints import EndpointRegistry, limits


def _server(tmp_path, *, endpoints=None, models=None) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    s.endpoints = endpoints or {
        "or": EndpointConfig(name="or", kind="openai",
                             base_url="https://openrouter.ai/api/v1", context_chars=200_000)}
    s.models = models or {
        "kimi": ModelConfig(name="kimi", endpoint="or", model="moonshot/kimi-k3")}
    return s


def _cache(server, **rows):
    limits.cache_path(server.routines_home).parent.mkdir(parents=True, exist_ok=True)
    limits.cache_path(server.routines_home).write_text(
        json.dumps({"fetched": "2026-09-05T09:00:00+00:00", **rows}), encoding="utf-8")


# ---- precedence ---------------------------------------------------------------------------------

def test_a_discovered_window_replaces_the_endpoint_guess(tmp_path):
    """The live case: an endpoint declaring 200,000 chars (50k tokens) in front of a model whose
    provider reports a 256k-token window — 4.8% of it in use."""
    server = _server(tmp_path)
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 262_144,
                                              "max_output_tokens": 32_000, "source": "openrouter"}})
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.context_chars == 262_144 * 4          # the provider's figure, in the engine's unit
    assert ref.max_tokens == 32_000


def test_an_explicit_config_value_still_wins(tmp_path):
    """Sizing DOWN is a deliberate budget and window.py already promises to honour it —
    discovery replaces the ABSENCE of a value, not the presence of one."""
    server = _server(tmp_path, models={
        "kimi": ModelConfig(name="kimi", endpoint="or", model="moonshot/kimi-k3",
                            context_chars=40_000, max_tokens=4_096)})
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 262_144,
                                              "max_output_tokens": 32_000, "source": "openrouter"}})
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.context_chars == 40_000 and ref.max_tokens == 4_096


def test_a_model_the_provider_does_not_list_falls_back_to_the_floor(tmp_path):
    server = _server(tmp_path)
    _cache(server)                                    # nothing discovered
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.context_chars == 200_000               # the endpoint default, as before
    assert ref.max_tokens == 16_384                   # DEFAULT_MODEL_MAX_TOKENS


def test_resolution_never_touches_the_network(tmp_path, monkeypatch):
    """`resolve` is on the per-turn path. A probe there would put a provider outage between the
    model and every single turn."""
    def boom(*_a, **_k):
        raise AssertionError("resolve() made a network call")
    monkeypatch.setattr(limits.httpx, "get", boom)
    monkeypatch.setattr(limits.httpx, "post", boom)
    EndpointRegistry(_server(tmp_path)).resolve("kimi")


# ---- the output cap is CLAMPED, not maxed --------------------------------------------------------

def test_the_output_cap_is_clamped_to_what_the_harness_needs(tmp_path, monkeypatch):
    server = _server(tmp_path)
    monkeypatch.setattr(limits, "_get", lambda *a, **k: {"data": [
        {"id": "moonshot/kimi-k3", "context_length": 1_310_720,
         "top_provider": {"max_completion_tokens": 943_718}}]})
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")
    assert row["context_tokens"] == 1_310_720                  # the window: verbatim
    assert row["max_output_tokens"] == limits.ENGINE_OUTPUT_CEILING   # the output: clamped
    assert row["provider_max_output_tokens"] == 943_718        # …and what it really was, kept


def test_a_small_provider_output_limit_is_taken_as_is(tmp_path, monkeypatch):
    server = _server(tmp_path)
    monkeypatch.setattr(limits, "_get", lambda *a, **k: {"data": [
        {"id": "moonshot/kimi-k3", "context_length": 65_536,
         "top_provider": {"max_completion_tokens": 8_192}}]})
    limits.refresh(server, force=True)
    assert limits.lookup(server.routines_home, "or",
                         "moonshot/kimi-k3")["max_output_tokens"] == 8_192


# ---- discovery per provider ----------------------------------------------------------------------

def test_nanogpt_is_read_from_its_own_route(tmp_path, monkeypatch):
    """The OpenAI-compatible /api/v1/models carries no limits; only nano-gpt's own route does."""
    server = _server(tmp_path, endpoints={
        "ng": EndpointConfig(name="ng", kind="openai", base_url="https://nano-gpt.com/api/v1")},
        models={"glm": ModelConfig(name="glm", endpoint="ng", model="z-ai/glm-5.2")})
    seen = {}

    def fake(url, headers=None):
        seen["url"] = url
        return {"models": {"text": {"z-ai/glm-5.2": {"maxInputTokens": 1_048_576,
                                                     "maxOutputTokens": 96_000}}}}
    monkeypatch.setattr(limits, "_get", fake)
    limits.refresh(server, force=True)
    assert seen["url"] == "https://nano-gpt.com/api/models"
    row = limits.lookup(server.routines_home, "ng", "z-ai/glm-5.2")
    assert row["context_tokens"] == 1_048_576 and row["source"] == "nanogpt"


def test_a_kind_with_no_metadata_api_uses_the_static_table(tmp_path, monkeypatch):
    """claude-cli has no models API, no metadata command, and its overflow prose carries no
    figure — so the table is the honest answer, and it is labelled as one."""
    server = _server(tmp_path, endpoints={
        "claude": EndpointConfig(name="claude", kind="claude-cli", context_chars=2_000_000)},
        models={"opus": ModelConfig(name="opus", endpoint="claude", model="opus")})
    monkeypatch.setattr(limits, "_get", lambda *a, **k: None)
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "claude", "opus")
    assert row["context_tokens"] == 200_000 and row["source"] == "table"
    # …and it CORRECTS the endpoint's 2,000,000-char claim (a 500k-token window no Claude has)
    _ep, ref = EndpointRegistry(server).resolve("opus")
    assert ref.context_chars == 800_000


def test_a_failed_probe_keeps_what_was_already_known(tmp_path, monkeypatch):
    """One bad fetch must not forget a figure that is still true — otherwise a provider blip
    silently shrinks every model on it."""
    server = _server(tmp_path)
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 262_144,
                                              "max_output_tokens": 32_000, "source": "openrouter"}})
    monkeypatch.setattr(limits, "_get", lambda *a, **k: None)
    out = limits.refresh(server, force=True)
    assert out["misses"] == ["or/moonshot/kimi-k3"]
    assert limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")["context_tokens"] \
        == 262_144


def test_a_dead_provider_never_raises(tmp_path, monkeypatch):
    server = _server(tmp_path)

    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")
    monkeypatch.setattr(limits.httpx, "get", boom)
    assert limits.refresh(server, force=True)["written"] == 0


def test_the_ttl_stops_a_refresh_per_tick(tmp_path, monkeypatch):
    server = _server(tmp_path)
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 1, "max_output_tokens": 1}})
    assert limits.stale(server) is False
    calls = []
    monkeypatch.setattr(limits, "_get", lambda *a, **k: calls.append(1) or None)
    limits.refresh(server)                       # inside the TTL → no fetch at all
    assert calls == []


@pytest.mark.parametrize(("base", "kind", "want"), [
    ("https://openrouter.ai/api/v1", "openai", "openrouter"),
    ("https://nano-gpt.com/api/v1", "openai", "nanogpt"),
    ("http://localhost:11434/v1", "openai", "ollama"),
    ("https://api.featherless.ai/v1", "openai", "openai"),
    ("", "anthropic", "table"),
    ("", "claude-cli", "table"),
])
def test_the_provider_is_sniffed_from_the_endpoint(base, kind, want):
    assert limits._provider(EndpointConfig(name="x", kind=kind, base_url=base)) == want
