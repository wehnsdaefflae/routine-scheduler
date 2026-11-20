"""Provider-discovered model limits — the operator's "max out the token window without setting
it up".

The two things worth pinning are the PRECEDENCE (an explicit config value is an operator sizing
on purpose and must still win) and that BOTH knobs are the route's published maximum (operator,
2026-11-20: "not below") — with one guard, for a published output figure that is the window
restated rather than a maximum (`routecatalogs.is_placeholder`): providers validate
`input + requested_output <= window`, so asking for it would leave the prompt nothing.
Gateway routes are pinned in test_route_limits.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from helpers import tmp_server
from rsched.config import EndpointConfig, ModelConfig, ServerConfig
from rsched.endpoints import EndpointRegistry, catalogs, limits, prices
from rsched.paths import atomic_write_json


def _server(tmp_path, *, endpoints=None, models=None) -> ServerConfig:
    endpoints = endpoints or {
        "or": EndpointConfig(name="or", kind="openai",
                             base_url="https://openrouter.ai/api/v1", context_tokens=200_000)}
    models = models or {"kimi": ModelConfig(name="kimi", endpoint="or", model="moonshot/kimi-k3")}
    return tmp_server(tmp_path, endpoints=endpoints, models=models)


def _cache(server, **rows):
    limits.cache_path(server.routines_home).parent.mkdir(parents=True, exist_ok=True)
    limits.cache_path(server.routines_home).write_text(
        json.dumps({"fetched": datetime.now(UTC).isoformat(), **rows}), encoding="utf-8")


# ---- precedence ---------------------------------------------------------------------------------

def test_a_discovered_window_replaces_the_endpoint_guess(tmp_path):
    """The live case: an endpoint declaring 200,000 tokens in front of a model whose
    provider reports a 256k-token window — 4.8% of it in use."""
    server = _server(tmp_path)
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 262_144,
                                              "max_output_tokens": 32_000, "source": "openrouter"}})
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.context_tokens == 262_144          # the provider and engine use the same token unit
    assert ref.max_tokens == 32_000


def test_an_explicit_config_value_still_wins(tmp_path):
    """Sizing DOWN is a deliberate budget and window.py already promises to honour it —
    discovery replaces the ABSENCE of a value, not the presence of one."""
    server = _server(tmp_path, models={
        "kimi": ModelConfig(name="kimi", endpoint="or", model="moonshot/kimi-k3",
                            context_tokens=40_000, max_tokens=4_096)})
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 262_144,
                                              "max_output_tokens": 32_000, "source": "openrouter"}})
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.context_tokens == 40_000 and ref.max_tokens == 4_096


def test_a_model_the_provider_does_not_list_falls_back_to_the_floor(tmp_path):
    server = _server(tmp_path)
    _cache(server)                                    # nothing discovered
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.context_tokens == 200_000               # the endpoint default, as before
    assert ref.max_tokens == 16_384                   # DEFAULT_MODEL_MAX_TOKENS


def test_resolution_never_touches_the_network(tmp_path, monkeypatch):
    """`resolve` is on the per-turn path. A probe there would put a provider outage between the
    model and every single turn."""
    def boom(*_a, **_k):
        raise AssertionError("resolve() made a network call")
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "post", boom)
    EndpointRegistry(_server(tmp_path)).resolve("kimi")


# ---- the output cap is the route's published MAXIMUM ---------------------------------------------

def _published(monkeypatch, window, top):
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: {"data": [
        {"id": "moonshot/kimi-k3", "context_length": window, "top_provider": top}]})


@pytest.mark.parametrize(("window", "published"), [
    (1_310_720, 943_718),     # a real maximum far above any harness ceiling: taken as is
    (65_536, 8_192),          # a small one: taken as is
])
def test_the_output_cap_is_the_published_maximum(tmp_path, monkeypatch, window, published):
    server = _server(tmp_path)
    _published(monkeypatch, window, {"max_completion_tokens": published})
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")
    assert row["context_tokens"] == window
    assert row["max_output_tokens"] == published == row["provider_max_output_tokens"]
    _ep, ref = EndpointRegistry(server).resolve("kimi")
    assert ref.max_tokens == published


@pytest.mark.parametrize("published", [943_718, 1_048_576])
def test_a_placeholder_output_figure_is_not_a_maximum(tmp_path, monkeypatch, published):
    """OpenRouter's ⌊0.9 × window⌋ placeholder, or the window itself, leaves the prompt nothing:
    with no other catalog to say the real figure, the output stays UNKNOWN — the model takes the
    endpoint's default — and what was published is kept beside it."""
    server = _server(tmp_path)
    _published(monkeypatch, 1_048_576, {"max_completion_tokens": published})
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")
    assert row["max_output_tokens"] is None
    assert row["provider_max_output_tokens"] == published
    assert EndpointRegistry(server).resolve("kimi")[1].max_tokens == 16_384


def test_a_window_without_a_maximum_claims_no_output_figure(tmp_path, monkeypatch):
    server = _server(tmp_path)
    _published(monkeypatch, 131_072, {})
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")
    assert row["context_tokens"] == 131_072 and row["max_output_tokens"] is None


def test_openrouter_is_sized_by_its_top_provider(tmp_path, monkeypatch):
    """The provider OpenRouter routes to can serve less than the row's headline window."""
    server = _server(tmp_path)
    _published(monkeypatch, 1_050_000,
               {"context_length": 1_048_576, "max_completion_tokens": 131_072})
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")
    assert (row["context_tokens"], row["max_output_tokens"]) == (1_048_576, 131_072)


# ---- discovery per provider ----------------------------------------------------------------------

def test_nanogpt_is_read_from_its_detailed_listing(tmp_path, monkeypatch):
    """The plain OpenAI-compatible /api/v1/models carries no limits; `?detailed=true` does."""
    server = _server(tmp_path, endpoints={
        "ng": EndpointConfig(name="ng", kind="openai", base_url="https://nano-gpt.com/api/v1")},
        models={"glm": ModelConfig(name="glm", endpoint="ng", model="z-ai/glm-5.2")})
    seen = {}

    def fake(url, headers=None):
        seen.setdefault("url", url)
        return {"data": [{"id": "z-ai/glm-5.2", "context_length": 1_048_576,
                          "max_output_tokens": 96_000}]}
    monkeypatch.setattr(catalogs, "get_json", fake)
    limits.refresh(server, force=True)
    assert seen["url"] == "https://nano-gpt.com/api/v1/models?detailed=true"
    row = limits.lookup(server.routines_home, "ng", "z-ai/glm-5.2")
    assert row["context_tokens"] == 1_048_576 and row["source"] == "nanogpt"
    assert row["max_output_tokens"] == 96_000


def test_anthropics_own_api_is_sized_by_the_static_table(tmp_path, monkeypatch):
    """Anthropic's listing needs a key; the table answers for its own API, and only there."""
    server = _server(tmp_path, endpoints={
        "claude": EndpointConfig(name="claude", kind="anthropic", context_tokens=2_000_000)},
        models={"opus": ModelConfig(name="opus", endpoint="claude", model="claude-opus-4-8")})
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: None)
    limits.refresh(server, force=True)
    row = limits.lookup(server.routines_home, "claude", "claude-opus-4-8")
    assert row["context_tokens"] == 1_000_000 and row["source"] == "table"
    # The table carries WINDOWS: no output maximum is claimed for a figure nobody published.
    assert row["max_output_tokens"] is None
    assert row["provider_max_output_tokens"] is None
    # The discovered model window takes precedence over the larger endpoint fallback.
    _ep, ref = EndpointRegistry(server).resolve("opus")
    assert ref.context_tokens == 1_000_000


def test_a_failed_probe_keeps_what_was_already_known(tmp_path, monkeypatch):
    """One bad fetch must not forget a figure that is still true — otherwise a provider blip
    silently shrinks every model on it."""
    server = _server(tmp_path)
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 262_144,
                                              "max_output_tokens": 32_000, "source": "openrouter"}})
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: None)
    out = limits.refresh(server, force=True)
    assert out["misses"] == ["or/moonshot/kimi-k3"]
    assert limits.lookup(server.routines_home, "or", "moonshot/kimi-k3")["context_tokens"] \
        == 262_144


def test_a_dead_provider_never_raises(tmp_path, monkeypatch):
    server = _server(tmp_path)

    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")
    monkeypatch.setattr(httpx, "get", boom)
    assert limits.refresh(server, force=True)["written"] == 0


def test_the_ttl_stops_a_refresh_per_tick(tmp_path, monkeypatch):
    server = _server(tmp_path)
    _cache(server, **{"or|moonshot/kimi-k3": {"context_tokens": 1, "max_output_tokens": 1}})
    atomic_write_json(prices.cache_path(server.routines_home), {
        "rows": {}, "checked": [f"{m.endpoint}|{m.model}" for m in server.models.values()]})
    assert limits.stale(server) is False
    calls = []
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: calls.append(1) or None)
    limits.refresh(server)                       # inside the TTL → no fetch at all
    assert calls == []


@pytest.mark.parametrize(("base", "kind", "want"), [
    ("https://openrouter.ai/api/v1", "openai", "openrouter"),
    ("https://nano-gpt.com/api/v1", "openai", "nanogpt"),
    ("http://localhost:11434/v1", "openai", "ollama"),
    ("https://api.featherless.ai/v1", "openai", "openai"),
    ("", "anthropic", "table"),
    ("https://api.anthropic.com", "anthropic", "table"),
    # An anthropic-WIRE proxy is not Anthropic: it serves whatever its upstreams do, so its
    # catalog is read like any other gateway's rather than assumed absent.
    ("http://cliproxy:8317", "anthropic", "openai"),
    ("http://127.0.0.1:8317", "anthropic", "openai"),
])
def test_the_provider_is_sniffed_from_the_endpoint(base, kind, want):
    assert catalogs.provider_of(EndpointConfig(name="x", kind=kind, base_url=base)) == want


def test_new_model_refreshes_inside_global_ttl(tmp_path, monkeypatch):
    server = _server(tmp_path)
    _cache(server, **{"old|id": {"context_tokens": 200000}})
    assert limits.stale(server)
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: None)
    limits.refresh(server)
    # Missing metadata is retried after the TTL, not on every scheduler tick.
    assert not limits.stale(server)


@pytest.mark.parametrize(("model", "expected"), [
    # the current lineup (platform.claude.com models overview, 2026-10-01)
    ("claude-fable-5-1", 1_000_000), ("claude-opus-5-5", 1_000_000),
    ("claude-sonnet-5-5", 1_000_000), ("claude-haiku-4-5-20251001", 200_000),
    ("claude-haiku-5-5", 1_000_000),
    # still served
    ("claude-mythos-5-1", 1_000_000), ("claude-sonnet-5", 1_000_000),
    ("claude-sonnet-4-6", 1_000_000), ("claude-opus-4-8", 1_000_000),
    ("claude-opus-4-5-20251101", 200_000), ("claude-sonnet-4-5-20250929", 200_000),
    # retired (model deprecations page): a request to one fails, so no window is claimed
    ("claude-opus-4-1-20250805", None), ("claude-3-haiku-20240307", None),
    ("claude-opus-99", None),
])
def test_claude_revision_windows(model, expected):
    assert limits._static_window(model) == expected


# ---- a proxy behind the anthropic wire ----------------------------------------------------------

def _proxy_server(tmp_path, models):
    return _server(tmp_path, endpoints={
        "codex-proxy": EndpointConfig(name="codex-proxy", kind="anthropic",
                                      base_url="http://cliproxy:8317", context_tokens=25_000)},
        models=models)


def test_an_anthropic_base_url_gets_the_catalog_one_segment_deeper():
    """The `openai` base_url already carries the /v1 (`…/api/v1`); the `anthropic` one does not,
    because the Messages adapter appends `/v1/messages` itself. Live proof: the proxy answers
    404 on /models and 200 on /v1/models, and a 404 reads exactly like "publishes no catalog".
    """
    anth = EndpointConfig(name="p", kind="anthropic", base_url="http://cliproxy:8317")
    oai = EndpointConfig(name="o", kind="openai", base_url="https://openrouter.ai/api/v1")
    assert catalogs.models_url(anth) == "http://cliproxy:8317/v1/models"
    assert catalogs.models_url(oai) == "https://openrouter.ai/api/v1/models"


def test_a_listed_id_with_no_published_limits_is_served_but_undiscovered(tmp_path, monkeypatch):
    """CLIProxyAPI's catalog is ids only — {id, object, created, owned_by}. So the model IS
    served and its limits are NOT discoverable, which are two different answers: the id set is
    recorded even though not one row carried a figure.
    """
    server = _proxy_server(tmp_path, {
        "astra": ModelConfig(name="astra", endpoint="codex-proxy", model="gpt-6-astra")})
    seen = {}

    def fake(url, headers=None):
        seen.setdefault("url", url)     # the proxy listing is asked first; the reference after
        return {"data": [{"id": "gpt-6-astra", "object": "model", "owned_by": "openai"},
                         {"id": "gpt-5.6-terra", "object": "model", "owned_by": "openai"}]}
    monkeypatch.setattr(catalogs, "get_json", fake)
    out = limits.refresh(server, force=True)

    assert seen["url"] == "http://cliproxy:8317/v1/models"
    assert out["misses"] == ["codex-proxy/gpt-6-astra"]      # no figures: still a limits miss
    assert limits.lookup(server.routines_home, "codex-proxy", "gpt-6-astra") is None
    assert limits.serves(server.routines_home, "codex-proxy", "gpt-6-astra") is True
    assert limits.serves(server.routines_home, "codex-proxy", "gpt-6-nope") is False


def test_a_claude_id_on_a_proxy_is_not_given_the_apis_window(tmp_path, monkeypatch):
    """The table is Anthropic's OWN API. A proxy serving a Claude id may serve it on a smaller
    window (a Claude subscription: claude-sonnet-4-6 at 200,000, not 1,000,000), so an id it
    lists without figures stays unknown — a gateway with routes is how it gets the route's.
    """
    server = _proxy_server(tmp_path, {
        "sonnet": ModelConfig(name="sonnet", endpoint="codex-proxy", model="claude-sonnet-5")})
    monkeypatch.setattr(catalogs, "get_json",
                        lambda *a, **k: {"data": [{"id": "claude-sonnet-5"}]})
    assert limits.refresh(server, force=True)["misses"] == ["codex-proxy/claude-sonnet-5"]
    assert limits.lookup(server.routines_home, "codex-proxy", "claude-sonnet-5") is None


def test_an_unanswered_catalog_keeps_the_previous_served_set(tmp_path, monkeypatch):
    """A provider down for one refresh must never turn into "this endpoint does not serve your
    models" — that would put a false "fix the id" in front of an operator whose id is fine.
    """
    server = _proxy_server(tmp_path, {
        "astra": ModelConfig(name="astra", endpoint="codex-proxy", model="gpt-6-astra")})
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: {"data": [{"id": "gpt-6-astra"}]})
    limits.refresh(server, force=True)
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: None)
    limits.refresh(server, force=True)
    assert limits.serves(server.routines_home, "codex-proxy", "gpt-6-astra") is True


def test_no_catalog_at_all_leaves_the_question_unanswered(tmp_path, monkeypatch):
    """Anthropic's own endpoint publishes no usable catalog, so `serves` says None — not False.
    Nothing may render an unasked question as a negative answer.
    """
    server = _server(tmp_path, endpoints={
        "claude": EndpointConfig(name="claude", kind="anthropic",
                                 base_url="https://api.anthropic.com")},
        models={"opus": ModelConfig(name="opus", endpoint="claude", model="claude-opus-4-8")})
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: None)
    limits.refresh(server, force=True)
    assert limits.serves(server.routines_home, "claude", "claude-opus-4-8") is None


def test_the_served_key_is_not_counted_as_a_model_row(tmp_path, monkeypatch):
    """`written`/`skipped` mean model rows; the cache also carries fetched/checked_models/served."""
    server = _proxy_server(tmp_path, {
        "astra": ModelConfig(name="astra", endpoint="codex-proxy", model="gpt-6-astra")})
    monkeypatch.setattr(catalogs, "get_json", lambda *a, **k: {"data": [{"id": "gpt-6-astra"}]})
    assert limits.refresh(server, force=True)["written"] == 0     # served, but no figures
