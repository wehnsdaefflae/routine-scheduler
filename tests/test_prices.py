"""The notional cost of a completion (endpoints/prices.py) — one source per route, no guesses.

A subscription transport reports `cost: 0.0` for every call, so before this the instance could
not say what a run cost at all. The rules pinned here are the ones a "helpful" change would
break: a price is never invented for a model no source prices, a token class whose rate is not
published leaves the whole figure unpriced rather than partial, and a provider's own reported
bill is taken at its word.
"""

from __future__ import annotations

from helpers import tmp_server
from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import catalogs, prices
from rsched.usage_log import spend

REFERENCE = {"data": [
    {"id": "anthropic/claude-opus-5", "pricing": {
        "prompt": "0.000005", "completion": "0.000025",
        "input_cache_read": "0.0000005", "input_cache_write": "0.00000625"}},
    {"id": "anthropic/claude-haiku-4.5", "pricing": {
        "prompt": "0.000001", "completion": "0.000005",
        "input_cache_read": "0.0000001", "input_cache_write": "0.00000125"}},
    {"id": "z-ai/glm-5.3", "pricing": {"prompt": "0.000000079", "completion": "0.0000048",
                                       "input_cache_read": "0.000000078"}},
    {"id": "openrouter/auto-beta", "pricing": {"prompt": "-1", "completion": "-1"}},
]}


def _server(tmp_path):
    return tmp_server(
        tmp_path,
        endpoints={
            "proxy": EndpointConfig(name="proxy", kind="anthropic", base_url="http://cliproxy:8317"),
            "or": EndpointConfig(name="or", kind="openai", base_url="https://openrouter.ai/api/v1"),
        },
        models={"opus": ModelConfig(endpoint="proxy", model="claude-opus-5"),
                "haiku": ModelConfig(endpoint="proxy", model="claude-haiku-4-5-20251001"),
                "glm": ModelConfig(endpoint="or", model="z-ai/glm-5.3"),
                "auto": ModelConfig(endpoint="or", model="openrouter/auto-beta"),
                "mystery": ModelConfig(endpoint="proxy", model="nobody-prices-me")})


def test_a_subscription_model_is_priced_at_its_list_price(tmp_path):
    server = _server(tmp_path)
    out = prices.refresh(server, REFERENCE)
    home = server.routines_home
    assert prices.price_for(home, "proxy", "claude-opus-5") == (5.0, 25.0, 0.5, 6.25)
    # the proxy's dated snapshot id and OpenRouter's dotted name are one model
    assert prices.price_for(home, "proxy", "claude-haiku-4-5-20251001") == (1.0, 5.0, 0.1, 1.25)
    # an unpublished price is UNPRICED and named — never borrowed, never defaulted
    assert prices.price_for(home, "proxy", "nobody-prices-me") is None
    # a variable-price route (-1) has no list price either: its reported bill prices each call
    assert set(out["unpriced"]) == {"proxy/nobody-prices-me", "or/openrouter/auto-beta"}
    assert prices.unpriced(home) == out["unpriced"]


def test_a_metered_model_is_priced_by_its_own_provider_for_its_exact_id(tmp_path):
    server = _server(tmp_path)
    prices.refresh(server, REFERENCE)
    rates = prices.price_for(server.routines_home, "or", "z-ai/glm-5.3")
    assert rates == (0.079, 4.8, 0.078, None)       # no cache-write rate published: None


def test_a_token_class_without_a_published_rate_leaves_the_figure_unpriced():
    rates = (0.079, 4.8, 0.078, None)
    assert prices.notional({"in": 1_000_000, "out": 1_000_000}, rates) == 4.879
    # one cache write the provider publishes no rate for: a partial sum would read as the whole
    assert prices.notional({"in": 1_000_000, "cache_write": 10}, rates) is None


def test_the_whole_context_volume_is_priced():
    """grant-hunter, 2026-10-17, on Opus 5: 214,028 tokens read as $0.00 on the subscription."""
    usage = {"in": 16_252, "out": 197_776, "cached_in": 25_944_428, "cache_write": 881_910}
    assert prices.notional(usage, (5.0, 25.0, 0.5, 6.25)) == 23.509812


def test_a_reported_bill_is_taken_at_its_word(tmp_path):
    usage = {"in": 10, "out": 10, "cost": 0.0421}
    prices.price_usage(tmp_path, "or", "openrouter/auto-beta", usage)
    assert usage["notional"] == 0.0421


def test_an_unpriced_completion_carries_no_notional_at_all(tmp_path):
    usage = {"in": 10, "out": 10}
    prices.price_usage(tmp_path, "proxy", "nobody-prices-me", usage)
    assert "notional" not in usage


def test_a_source_that_did_not_answer_keeps_the_last_published_rows(tmp_path):
    server = _server(tmp_path)
    prices.refresh(server, REFERENCE)
    prices.refresh(server, {})                       # the catalog was down for one refresh
    assert prices.price_for(server.routines_home, "proxy", "claude-opus-5") == (5.0, 25.0, 0.5, 6.25)


def test_nanogpt_publishes_its_own_prices(tmp_path, monkeypatch):
    server = tmp_server(
        tmp_path,
        endpoints={"nano": EndpointConfig(name="nano", kind="openai",
                                          base_url="https://nano-gpt.com/api/v1")},
        models={"glmu": ModelConfig(endpoint="nano", model="z-ai/glm-5.3-uncensored")})
    monkeypatch.setattr(catalogs, "nanogpt_models", lambda ep: {"z-ai/glm-5.3-uncensored": {
        "pricing": {"prompt_per_million": 1.25, "completion_per_million": 2.25,
                    "cacheReadInputPer1kTokens": 0.0003}}})
    prices.refresh(server, REFERENCE)
    assert prices.price_for(server.routines_home, "nano", "z-ai/glm-5.3-uncensored") == \
        (1.25, 2.25, 0.3, None)


def test_a_usage_row_carries_the_whole_volume_and_its_notional_cost():
    row = spend({"in": 100, "out": 50, "cached_in": 4_000, "cache_write": 300,
                 "notional": 0.123456789})
    assert row == {"tokens": 150, "cost": 0.0, "cached_in": 4_000, "cache_write": 300,
                   "volume": 4_450, "notional_cost": 0.123457}
    assert spend({"in": 1, "out": 1})["notional_cost"] is None     # unpriced, not free


def test_the_seam_prices_every_completion(tmp_path):
    from rsched.endpoints.base import Completion
    from rsched.endpoints.instrument import InstrumentedEndpoint

    class Inner:
        name = "proxy"

        def complete(self, messages, **kw):
            return Completion(text="{}", usage={"in": 1_000_000, "out": 0})

    server = _server(tmp_path)
    prices.refresh(server, REFERENCE)
    comp = InstrumentedEndpoint(Inner(), server.routines_home).complete([], model="claude-opus-5")
    assert comp.usage["notional"] == 5.0


def test_a_model_never_priced_makes_the_limits_cache_stale(tmp_path, monkeypatch):
    """0.428.0 shipped the price cache behind the limits TTL: a limits cache written before the
    deploy stayed fresh for a day, so no run was priced until it expired."""
    from rsched.endpoints import limits
    server = _server(tmp_path)
    monkeypatch.setattr(limits, "_missing_models", lambda *_: False)
    monkeypatch.setattr(limits, "load", lambda _h: {"fetched": "2999-01-01T00:00:00+00:00"})
    assert prices.missing(server) and limits.stale(server)
    prices.refresh(server, REFERENCE)
    assert not prices.missing(server) and not limits.stale(server)
    server.models["new"] = ModelConfig(endpoint="proxy", model="claude-new")
    assert prices.missing(server)       # a model added in Settings is priced at the next tick
