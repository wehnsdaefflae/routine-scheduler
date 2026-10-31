"""A bill the wire REPORTS is the usage's `cost` — on both adapters (0.484.0).

Every model call goes through an Otari gateway, which prices each request itself and returns it
in the usage block as `cost_usd`, a six-decimal USD STRING (a subscription model there is
`"0.000000"`, its real bill). The anthropic adapter read no reported cost at all, so a
pay-per-use Claude model through the gateway was metered only by a reference list price — and
`claude-opus-5-metered`, the gateway's name for the paid route, found none. The rules pinned
here: the reported figure is read on both wires (OpenRouter's numeric `cost` first), a missing
or malformed one is NO figure rather than a zero, a re-asked turn sums it as dollars, and a
`-metered` id is the same model as its subscription twin for its list price and its window.
"""

from __future__ import annotations

import httpx
import pytest

import rsched.endpoints.anthropic_api as anth_mod
import rsched.endpoints.openai_compat as oai_mod
from helpers import tmp_server
from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import catalogs, decisions_systemone, limits, prices
from rsched.endpoints.anthropic_api import AnthropicEndpoint
from rsched.endpoints.decisions import DecisionQuestion
from rsched.endpoints.openai_compat import OpenAICompatEndpoint
from rsched.endpoints.usage import dollars_spent, reported_cost, summed

MESSAGES = [{"role": "user", "content": "hi"}]
GATEWAY = "http://172.17.0.1:4000/api"
#: The usage blocks as the gateway returned them, measured 2026-10-31.
ANTHROPIC_USAGE = {"input_tokens": 3, "output_tokens": 1, "cache_creation_input_tokens": 0,
                   "cache_read_input_tokens": 0, "cost_usd": "0.000005",
                   "pricing_source": "deployment"}
OPENAI_USAGE = {"prompt_tokens": 3, "completion_tokens": 1, "cost_usd": "0.000005",
                "pricing_source": "deployment"}


@pytest.mark.parametrize(("raw", "expected"), [
    ({"cost_usd": "0.000005"}, 0.000005),                     # the gateway's string
    ({"cost_usd": "0.000000"}, 0.0),                          # a subscription's true bill
    ({"cost": 5.1e-06, "cost_usd": "0.000005"}, 5.1e-06),     # OpenRouter's own figure first
    ({"cost": 0.0002}, 0.0002),
    ({"cost": None, "cost_usd": "0.000005"}, 0.000005),       # an absent `cost` falls through
    ({"cost": "n/a", "cost_usd": "0.000005"}, 0.000005),      # so does a malformed one
    ({"cost_usd": " 0.25 "}, 0.25),
    ({}, None),                                               # nothing reported
    ({"cost_usd": ""}, None),
    ({"cost_usd": "abc"}, None),
    ({"cost_usd": "NaN"}, None),
    ({"cost_usd": "Infinity"}, None),
    ({"cost_usd": "-0.5"}, None),                             # a bill is never negative
    ({"cost_usd": True}, None),                               # a flag is not a number
    ({"cost_usd": {"usd": 1}}, None),
])
def test_a_reported_bill_is_read_and_a_malformed_one_is_no_figure(raw, expected):
    assert reported_cost(raw) == expected


def _anth():
    return AnthropicEndpoint(EndpointConfig(name="otari", kind="anthropic", base_url=GATEWAY,
                                            api_key="k", context_tokens=200_000))


def _anth_reply(usage: dict) -> dict:
    return {"content": [{"type": "tool_use", "name": "action",
                         "input": {"say": "s", "kind": "finish"}}], "usage": usage}


def _post(*payloads: dict):
    it = iter(payloads)
    return lambda *a, **k: _Reply(next(it))


class _Reply:
    def __init__(self, payload: dict):
        self.status_code = 200
        self.headers: dict = {}
        self._payload = payload
        self.text = "{}"

    def json(self) -> dict:
        return self._payload


def test_the_anthropic_adapter_reads_the_gateways_cost_usd(monkeypatch):
    monkeypatch.setattr(anth_mod.httpx, "post", _post(_anth_reply(ANTHROPIC_USAGE)))
    c = _anth().complete(MESSAGES, model="claude-opus-5-metered", schema={"type": "object"})
    assert c.usage == {"in": 3, "out": 1, "cost": 0.000005}


@pytest.mark.parametrize("usage", [
    {"input_tokens": 3, "output_tokens": 1},
    {"input_tokens": 3, "output_tokens": 1, "cost_usd": "unpriced"},
])
def test_the_anthropic_adapter_invents_no_cost(monkeypatch, usage):
    monkeypatch.setattr(anth_mod.httpx, "post", _post(_anth_reply(usage)))
    c = _anth().complete(MESSAGES, model="claude-opus-5", schema={"type": "object"})
    assert c.usage == {"in": 3, "out": 1}


def test_a_reasked_turn_bills_both_requests_in_dollars(monkeypatch):
    """The forced re-ask sums the two requests' usage. It used to sum every key as an integer,
    which would have turned two sub-dollar bills into 0."""
    unreadable = {"content": [{"type": "thinking", "thinking": "", "signature": "s"}],
                  "stop_reason": "tool_use",
                  "usage": {"input_tokens": 5, "output_tokens": 40, "cost_usd": "0.001250"}}
    monkeypatch.setattr(anth_mod.httpx, "post",
                        _post(unreadable, _anth_reply(ANTHROPIC_USAGE)))
    c = _anth().complete(MESSAGES, model="claude-opus-5-metered", schema={"type": "object"})
    assert c.stop_details == {"forced_reask": "thinking"}
    assert c.usage == {"in": 8, "out": 41, "cost": 0.001255}


def test_summed_folds_every_key_as_its_own_kind():
    assert summed({"in": 1, "out": 2, "cost": 0.4}, {"in": 3, "out": 4, "cached_in": 9,
                                                     "cost": 0.35}) == {
        "in": 4, "out": 6, "cached_in": 9, "cost": 0.75}
    assert summed({}, {}) == {"in": 0, "out": 0}


def _oai(base_url=f"{GATEWAY}/v1"):
    return OpenAICompatEndpoint(EndpointConfig(name="otari-openai", kind="openai",
                                               base_url=base_url, api_key="k",
                                               context_tokens=200_000))


def _oai_reply(usage: dict) -> dict:
    return {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": usage}


def test_the_openai_adapter_takes_cost_usd_when_no_cost_is_reported(monkeypatch):
    monkeypatch.setattr(oai_mod.httpx, "post", _post(_oai_reply(OPENAI_USAGE)))
    assert _oai().complete(MESSAGES, model="m").usage == {"in": 3, "out": 1, "cost": 0.000005}


def test_the_openai_adapter_prefers_openrouters_own_cost(monkeypatch):
    usage = {**OPENAI_USAGE, "cost": 5.1e-06}
    monkeypatch.setattr(oai_mod.httpx, "post", _post(_oai_reply(usage)))
    assert _oai().complete(MESSAGES, model="m").usage["cost"] == 5.1e-06


def test_the_openai_adapter_survives_a_malformed_cost(monkeypatch):
    """`float(usage["cost"])` raised on a value it could not parse, ending the call."""
    usage = {"prompt_tokens": 3, "completion_tokens": 1, "cost": "n/a"}
    monkeypatch.setattr(oai_mod.httpx, "post", _post(_oai_reply(usage)))
    assert _oai().complete(MESSAGES, model="m").usage == {"in": 3, "out": 1}


def test_a_decision_through_the_gateway_reads_cost_usd(monkeypatch):
    def fake(url, payload, headers, timeout, *, name):
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "answers": {"q": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 4, "output_tokens": 2, "cost_usd": "0.000020"}})
    monkeypatch.setattr(decisions_systemone, "post_json", fake)
    from rsched.config.decisionconf import DecisionEndpointConfig
    endpoint = decisions_systemone.SystemOneDecisions(DecisionEndpointConfig(
        name="otari", protocol="systemone", base_url=f"{GATEWAY}/v1", api_key="k"))
    out = endpoint.decide("x", [], [DecisionQuestion("q", "yes_no", "?")], model="m", timeout=5)
    assert out.usage == {"in": 4, "out": 2, "cost": 0.00002}


# --- the `-metered` route is the same model -----------------------------------------------------
@pytest.mark.parametrize(("model_id", "name"), [
    ("claude-opus-5-metered", "claude-opus-5"),
    ("Claude-Opus-5-5-Metered", "claude-opus-5-5"),
    ("gemini-4-argon-high-metered", "gemini-4-argon"),        # the suffix, then the level
    ("vendor/claude-opus-5-metered:batch", "claude-opus-5"),
    ("metered", "metered"),                                   # a whole id is not a suffix
    ("claude-metered-5", "claude-metered-5"),                 # nor is a word inside one
    ("claude-opus-5-unmetered", "claude-opus-5-unmetered"),
])
def test_a_metered_gateway_id_reduces_to_its_models_name(model_id, name):
    assert catalogs.reference_name(model_id) == name


REFERENCE = {"data": [
    {"id": "anthropic/claude-opus-5", "context_length": 1_000_000,
     "top_provider": {"max_completion_tokens": 128_000},
     "pricing": {"prompt": "0.000005", "completion": "0.000025",
                 "input_cache_read": "0.0000005", "input_cache_write": "0.00000625"}},
    {"id": "z-ai/glm-5.3", "context_length": 200_000,
     "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
]}


def _gateway_server(tmp_path):
    return tmp_server(
        tmp_path,
        endpoints={"otari": EndpointConfig(name="otari", kind="anthropic", base_url=GATEWAY),
                   "otari-openai": EndpointConfig(name="otari-openai", kind="openai",
                                                  base_url=f"{GATEWAY}/v1")},
        models={"opus": ModelConfig(endpoint="otari", model="claude-opus-5"),
                "opus-paid": ModelConfig(endpoint="otari", model="claude-opus-5-metered"),
                "glm-paid": ModelConfig(endpoint="otari-openai", model="glm-5.3-metered")})


def test_a_metered_gateway_model_gets_its_models_list_price(tmp_path):
    server = _gateway_server(tmp_path)
    out = prices.refresh(server, REFERENCE)
    home = server.routines_home
    assert prices.price_name("claude-opus-5-metered") == "claude-opus-5"
    assert prices.price_for(home, "otari", "claude-opus-5-metered") == (5.0, 25.0, 0.5, 6.25)
    assert prices.price_for(home, "otari-openai", "glm-5.3-metered") == (1.0, 2.0, None, None)
    assert out["unpriced"] == []


def test_a_metered_gateway_model_gets_its_models_window():
    reference = limits._Reference()
    reference._body = REFERENCE
    assert reference.get("glm-5.3-metered") == (200_000, None)
    assert reference.get("claude-opus-5-metered") == (1_000_000, 128_000)


def test_the_reported_bill_is_what_a_call_costs_and_a_zero_is_no_price(tmp_path):
    """Reported cost = the bill, notional = list price: a paid call's reported bill IS its
    notional; a subscription call's reported zero is true but prices nothing, so the list price
    stands — and both meter through `dollars_spent`."""
    server = _gateway_server(tmp_path)
    prices.refresh(server, REFERENCE)
    home = server.routines_home
    paid = {"in": 1_000_000, "out": 0, "cost": 4.2}
    prices.price_usage(home, "otari", "claude-opus-5-metered", paid)
    assert paid["notional"] == 4.2 and dollars_spent(paid) == 4.2
    subscription = {"in": 1_000_000, "out": 0, "cost": 0.0}
    prices.price_usage(home, "otari", "claude-opus-5", subscription)
    assert subscription["notional"] == 5.0 and subscription["cost"] == 0.0
    unreported = {"in": 1_000_000, "out": 0}
    prices.price_usage(home, "otari", "claude-opus-5-metered", unreported)
    assert unreported["notional"] == 5.0
