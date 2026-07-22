"""Model failover: the cooldown registry, catalog fallback chains, cooldown-aware role
resolution, per-model max_tokens resolution, and the engine's mid-turn failover — all
scripted, no network."""

import time

import pytest

from conftest import ScriptedEndpoint, finish, write_file
from rsched.config import (
    DEFAULT_MODEL_MAX_TOKENS,
    EndpointConfig,
    ModelConfig,
    ServerConfig,
)
from rsched.endpoints import EndpointRegistry, InstrumentedEndpoint, failover
from rsched.endpoints.base import EndpointError
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260717-080000"


# ---- the cooldown registry ------------------------------------------------------------------

def test_cooldown_mark_clear_expiry():
    assert not failover.is_cooling("ep", "m")
    failover.mark_failed("ep", "m")
    assert failover.is_cooling("ep", "m")
    failover.clear("ep", "m")
    assert not failover.is_cooling("ep", "m")
    failover.mark_failed("ep", "m", cooldown_s=0.01)
    assert failover.is_cooling("ep", "m")
    time.sleep(0.03)
    assert not failover.is_cooling("ep", "m")   # expired marks self-clean


def _ref(name, endpoint="ep", model=None):
    from rsched.config import ModelRef
    return object(), ModelRef(endpoint=endpoint, model=model or f"id-{name}", name=name)


def test_pick_and_next_after():
    chain = [_ref("a"), _ref("b"), _ref("c")]
    assert failover.pick(chain) is chain[0]
    failover.mark_failed("ep", "id-a")
    assert failover.pick(chain) is chain[1]
    failover.mark_failed("ep", "id-b")
    failover.mark_failed("ep", "id-c")
    assert failover.pick(chain) is chain[0]        # all cooling → the primary, never a stall
    # next_after walks strictly forward, skipping cooling members
    failover.reset()
    failover.mark_failed("ep", "id-b")
    assert failover.next_after(chain, chain[0][1]) is chain[2]
    assert failover.next_after(chain, chain[2][1]) is None      # chain exhausted
    assert failover.next_after(chain, _ref("ghost")[1]) is None  # unknown ref


# ---- catalog chains + max_tokens through the real registry -----------------------------------

def _catalog_server(routines_home) -> ServerConfig:
    s = ServerConfig(
        endpoints={"epA": EndpointConfig(kind="openai", base_url="http://127.0.0.1:1/v1",
                                         max_tokens=7000),
                   "epB": EndpointConfig(kind="openai", base_url="http://127.0.0.1:2/v1")},
        models={"prime": ModelConfig(endpoint="epA", model="m-a", max_tokens=9000,
                                     fallbacks=["backup"]),
                "plain": ModelConfig(endpoint="epA", model="m-p",
                                     fallbacks=["plain", "backup", "backup", "ghost"]),
                "backup": ModelConfig(endpoint="epB", model="m-b")},
        system_model="prime")
    for name, ep in s.endpoints.items():
        ep.name = name
    for name, mc in s.models.items():
        mc.name = name
    s.routines_home = routines_home
    s.libraries_home = routines_home.parent / "test-library"
    return s


def test_resolve_max_tokens_inheritance(tmp_path):
    reg = EndpointRegistry(_catalog_server(tmp_path / "routines"))
    assert reg.resolve("prime")[1].max_tokens == 9000        # the model's own value wins
    assert reg.resolve("plain")[1].max_tokens == 7000        # inherits the endpoint default
    assert reg.resolve("backup")[1].max_tokens == DEFAULT_MODEL_MAX_TOKENS


def test_resolve_chain_skips_bad_entries(tmp_path):
    reg = EndpointRegistry(_catalog_server(tmp_path / "routines"))
    # self-reference, duplicate, and unknown fallback names are all skipped
    assert [ref.name for _, ref in reg.resolve_chain("plain")] == ["plain", "backup"]
    assert [ref.name for _, ref in reg.resolve_chain("prime")] == ["prime", "backup"]
    assert [ref.name for _, ref in reg.resolve_chain("backup")] == ["backup"]


def test_for_model_avoids_cooling_provider(tmp_path):
    reg = EndpointRegistry(_catalog_server(tmp_path / "routines"))
    assert reg.for_model("main", {})[1].name == "prime"      # system_model fallback
    failover.mark_failed("epA", "m-a")
    assert reg.for_model("main", {})[1].name == "backup"     # resolve-time avoidance
    failover.mark_failed("epB", "m-b")
    assert reg.for_model("main", {})[1].name == "prime"      # all cooling → primary


def test_instrumented_endpoint_marks_provider_health_failures():
    # a retryable-class failure (outage/rate limit — the adapter's retries exhausted) cools
    ep = InstrumentedEndpoint(ScriptedEndpoint([EndpointError("overloaded", retryable=True)]))
    with pytest.raises(EndpointError):
        ep.complete([{"role": "user", "content": "hi"}], model="test-model")
    assert failover.is_cooling("scripted", "test-model")
    # a DETERMINISTIC failure (bad key / malformed request — a config error) must NOT cool:
    # a Settings probe with a wrong credential would otherwise poison resolution for 5 min
    ep2 = InstrumentedEndpoint(ScriptedEndpoint([EndpointError("bad key", auth=True)]))
    with pytest.raises(EndpointError):
        ep2.complete([{"role": "user", "content": "hi"}], model="cfg-model")
    assert not failover.is_cooling("scripted", "cfg-model")
    # a NON-EndpointError (a bug, not a provider failure) must not mark a cooldown
    ep3 = InstrumentedEndpoint(ScriptedEndpoint([ValueError("bug")]))
    with pytest.raises(ValueError, match="bug"):
        ep3.complete([{"role": "user", "content": "hi"}], model="other-model")
    assert not failover.is_cooling("scripted", "other-model")


# ---- the engine's mid-turn failover ----------------------------------------------------------

def _wire(monkeypatch, server, replies_by_endpoint) -> dict[str, ScriptedEndpoint]:
    """run_routine over the REAL EndpointRegistry chain/pick logic, with each configured
    endpoint served by its own ScriptedEndpoint (the transport is the only fake)."""
    import rsched.engine.loop as loop_mod
    import rsched.engine.runtime as runtime_mod

    monkeypatch.setattr(loop_mod, "POLL_S", 0.02)
    eps = {}
    for name, replies in replies_by_endpoint.items():
        eps[name] = ScriptedEndpoint(replies)
        eps[name].name = name   # cooldown marks key on the CONFIG endpoint name

    class ChainRegistry(EndpointRegistry):
        def get(self, name):
            return InstrumentedEndpoint(eps[name])

    monkeypatch.setattr(runtime_mod, "EndpointRegistry", lambda s: ChainRegistry(server))
    return eps


def test_run_fails_over_to_fallback_model(make_routine, monkeypatch):
    d = make_routine("failover")
    server = _catalog_server(d.parent)
    eps = _wire(monkeypatch, server, {
        "epA": [EndpointError("epA is down")],
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="served by the backup model")]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    # turn 1: prime fails mid-turn → backup serves it; turn 2: the cooling primary is
    # skipped at RESOLVE time, so epA is probed exactly once for the whole run
    assert len(eps["epA"].calls) == 1 and len(eps["epB"].calls) == 2
    # each call carried ITS model's resolved max_tokens (prime 9000, backup the default)
    assert eps["epA"].calls[0]["max_tokens"] == 9000
    assert eps["epB"].calls[0]["max_tokens"] == DEFAULT_MODEL_MAX_TOKENS
    events, _ = read_events(run_dir / "transcript.jsonl")
    switch = [e for e in events if e["type"] == "error" and e["payload"].get("failover")]
    assert switch and switch[0]["payload"]["failover"] == {
        "from": "prime", "to": "backup", "cooldown_s": failover.COOLDOWN_S}
    # every turn is attributed to the model that actually served it
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert [t["usage"]["model"] for t in turns] == ["epB/m-b", "epB/m-b"]
    assert failover.is_cooling("epA", "m-a")   # the failed provider is cooling


def test_run_fails_when_chain_exhausted(make_routine, monkeypatch):
    d = make_routine("exhausted")
    server = _catalog_server(d.parent)
    _wire(monkeypatch, server, {
        "epA": [EndpointError("epA is down")],
        "epB": [EndpointError("epB is down too")]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "failed"
    events, _ = read_events(run_dir / "transcript.jsonl")
    fin = next(e for e in events if e["type"] == "finish")
    assert "Endpoint failure" in fin["payload"]["summary"]


def test_resolve_time_avoidance_skips_cooling_primary(make_routine, monkeypatch):
    d = make_routine("avoid")
    server = _catalog_server(d.parent)
    eps = _wire(monkeypatch, server, {
        "epA": [],   # any call to epA would AssertionError (out of replies)
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="straight to the backup")]})
    failover.mark_failed("epA", "m-a")
    status, _ = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert eps["epA"].calls == []   # never probed while cooling


# ---- empty completions: the refusal gap ------------------------------------------------------

def test_empty_completions_fail_over_to_fallback(make_routine, monkeypatch):
    """Two consecutive EMPTY completions from one model engage the failover chain exactly
    like a hard EndpointError — same-model blind retries can never fix a broken/refusing
    model, so the turn moves on and the model cools (the refusal-gap fix)."""
    from rsched.endpoints.base import Completion

    d = make_routine("emptyfo")
    server = _catalog_server(d.parent)
    eps = _wire(monkeypatch, server, {
        "epA": [Completion(text="", stop_reason="refusal"),
                Completion(text="", stop_reason="refusal")],
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="served by the backup model")]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert len(eps["epA"].calls) == 2       # exactly two empties, then the chain advances
    events, _ = read_events(run_dir / "transcript.jsonl")
    empties = [e for e in events if e["type"] == "error"
               and "empty completion (no content" in e["payload"].get("message", "")]
    assert len(empties) == 2
    assert "stop_reason=refusal" in empties[0]["payload"]["message"]
    switch = [e for e in events if e["type"] == "error" and e["payload"].get("failover")]
    assert switch and switch[0]["payload"]["failover"]["to"] == "backup"
    assert failover.is_cooling("epA", "m-a")
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert [t["usage"]["model"] for t in turns] == ["epB/m-b", "epB/m-b"]


def test_empty_refusal_referred_to_uncensored(make_routine, monkeypatch):
    """An EMPTY completion whose stop_reason is `refusal` (a classifier refusal — there is
    no free text to sniff) is referred to the routine's `uncensored` model exactly like a
    free-text refusal: the referred action serves the turn and the referral is audited."""
    import json as _json

    import yaml as _yaml

    from rsched.endpoints.base import Completion

    d = make_routine("emptyref")
    cfg = _yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["models"] = {"uncensored": "unc"}
    (d / "routine.yaml").write_text(_yaml.safe_dump(cfg), encoding="utf-8")
    server = _catalog_server(d.parent)
    server.endpoints["epU"] = __import__("rsched.config", fromlist=["EndpointConfig"]) \
        .EndpointConfig(kind="openai", base_url="http://127.0.0.1:3/v1")
    server.endpoints["epU"].name = "epU"
    server.models["unc"] = __import__("rsched.config", fromlist=["ModelConfig"]) \
        .ModelConfig(endpoint="epU", model="m-u")
    server.models["unc"].name = "unc"
    eps = _wire(monkeypatch, server, {
        "epA": [Completion(text="", stop_reason="refusal"),
                finish(summary="finished after the referred turn")],
        "epB": [],
        "epU": [write_file("state/probe.txt", say="the uncensored model serves the turn")]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert len(eps["epU"].calls) == 1 and len(eps["epA"].calls) == 2
    events, _ = read_events(run_dir / "transcript.jsonl")
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert turns[0].get("referred") is True
    assert turns[0]["usage"]["model"] == "epU/m-u"
    status_json = _json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status_json["referrals"] == 1
