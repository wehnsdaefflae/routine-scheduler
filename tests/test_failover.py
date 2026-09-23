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


def test_a_provider_that_asked_for_longer_is_cooled_for_longer():
    """`Retry-After` is the provider STATING when it will serve again; the 5-minute default
    is a guess. Re-probing before the stated time is a guaranteed failure — three retries
    and a model switch spent to learn what the header already said. (`with_retries` caps the
    same hint at 30 s, but that bounds how long ONE attempt may sleep; nothing waits here.)"""
    assert failover.cooldown_for(EndpointError("boom")) == failover.COOLDOWN_S
    assert failover.cooldown_for(
        EndpointError("429", retryable=True, retry_after=30)) == failover.COOLDOWN_S
    assert failover.cooldown_for(
        EndpointError("429", retryable=True, retry_after=3600)) == 3600

    # and the instrumentation seam — the one place a transport failure is cooled — uses it
    inner = ScriptedEndpoint([
        EndpointError("rate limited", retryable=True, retry_after=0.05)])
    inner.name = "epQ"
    with pytest.raises(EndpointError):
        InstrumentedEndpoint(inner).complete([{"role": "user", "content": "x"}], model="m-q")
    assert failover.is_cooling("epQ", "m-q")
    time.sleep(0.07)                      # the hint was shorter than the default…
    assert failover.is_cooling("epQ", "m-q")   # …so the default still governs


def _ref(name, endpoint="ep", model=None, **attrs):
    from rsched.config import ModelRef
    return object(), ModelRef(endpoint=endpoint, model=model or f"id-{name}", name=name,
                              **attrs)


#: every chain member takes anything — the capability filter is exercised on its own below
ANY_REQUEST = {"has_media": False, "prompt_tokens": 0}


def test_pick_and_next_after():
    chain = [_ref("a"), _ref("b"), _ref("c")]
    assert failover.pick(chain) is chain[0]
    failover.mark_failed("ep", "id-a")
    assert failover.pick(chain) is chain[1]
    failover.mark_failed("ep", "id-b")
    failover.mark_failed("ep", "id-c")
    assert failover.pick(chain) is chain[0]   # all cooling → the head, never a stall
    # next_after walks strictly forward, skipping cooling members
    failover.reset()
    failover.mark_failed("ep", "id-b")
    assert failover.next_after(chain, chain[0][1], **ANY_REQUEST) is chain[2]
    assert failover.next_after(chain, chain[2][1], **ANY_REQUEST) is None   # exhausted
    assert failover.next_after(chain, _ref("ghost")[1], **ANY_REQUEST) is None  # unknown ref


def test_pick_never_walks_back_up_a_chain_it_has_left():
    """A cooldown expires; a weekly quota does not. Once the engine has ABANDONED a member,
    `pick` starts from the one now serving — so a quota-exhausted primary cannot flap back
    in five minutes later, taking a cold cache write on both models with it (123 such swings
    across 28 live runs in ten days)."""
    chain = [_ref("a"), _ref("b"), _ref("c")]
    assert failover.pick(chain) is chain[0]
    # the primary fails hard mid-turn and the chain advances
    failover.mark_failed("ep", "id-a", cooldown_s=0.01)
    assert failover.next_after(chain, chain[0][1], **ANY_REQUEST) is chain[1]
    time.sleep(0.03)                                    # the cooldown lapses…
    assert not failover.is_cooling("ep", "id-a")
    assert failover.pick(chain) is chain[1]             # …and the run stays where it is
    # a fresh process (a fresh run) probes the head again — that is what lifts an outage
    failover.reset()
    assert failover.pick(chain) is chain[0]


def test_only_a_deliberate_abandonment_moves_the_serving_mark():
    """`pick` READS the mark and never writes it, and that is the whole safety of the
    scheme: the mark is keyed by chain HEAD and several roles resolve one head (a routine's
    `main` and its `llm` role on the same model). A write from `pick` would let one
    transient 5xx on a cheap subcall demote the MAIN turn loop for the rest of the run."""
    chain = [_ref("a"), _ref("b"), _ref("c")]
    failover.mark_failed("ep", "id-a", cooldown_s=0.01)
    assert failover.pick(chain) is chain[1]             # a subcall steps around the outage
    time.sleep(0.03)
    assert failover.pick(chain) is chain[0]             # …and the head is picked up again


def test_next_after_prefers_a_member_that_can_take_this_request():
    """Images and prompt size travel WITH the request, so a rung that cannot hold them is
    passed over while a capable one remains: trying it costs a round trip AND marks a
    healthy model cooling for five minutes. A preference, never a veto — when nothing fits,
    the next member is still taken so the engine can repair the request."""
    chain = [_ref("head", multimodal=True, context_tokens=200_000),
             _ref("text-only", multimodal=False, context_tokens=200_000),
             _ref("small", multimodal=True, context_tokens=32_768),
             _ref("roomy", multimodal=True, context_tokens=200_000)]
    # an image turn skips the text-only rung
    assert failover.next_after(chain, chain[0][1],
                               has_media=True, prompt_tokens=0) is chain[2]
    failover.reset()
    # a 165k prompt skips the 32k rung (its own output cap is reserved first)
    assert failover.next_after(chain, chain[1][1],
                               has_media=False, prompt_tokens=165_078) is chain[3]
    failover.reset()
    # nothing after `small` fits → the first usable one is taken anyway, never "exhausted"
    assert failover.next_after(chain[:3], chain[1][1],
                               has_media=False, prompt_tokens=165_078) is chain[2]


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


def _nested_server(routines_home) -> ServerConfig:
    """The fleet's real shape (R1504): a tier whose only fallback is a sibling on the SAME
    endpoint, which itself declares the models that actually reach a different provider.
    """
    s = ServerConfig(
        endpoints={"proxy": EndpointConfig(kind="openai", base_url="http://127.0.0.1:1/v1"),
                   "other": EndpointConfig(kind="openai", base_url="http://127.0.0.1:2/v1")},
        models={"astra": ModelConfig(endpoint="proxy", model="m-astra", fallbacks=["opus"]),
                "opus": ModelConfig(endpoint="proxy", model="m-opus",
                                    fallbacks=["glm-a", "glm-b"]),
                "glm-a": ModelConfig(endpoint="other", model="m-glm-a"),
                "glm-b": ModelConfig(endpoint="other", model="m-glm-b", fallbacks=["astra"]),
                # a rung that cannot resolve: its endpoint does not exist
                "broken": ModelConfig(endpoint="nope", model="m-x", fallbacks=["glm-a"]),
                "head": ModelConfig(endpoint="proxy", model="m-h",
                                    fallbacks=["broken", "opus"])},
        system_model="astra")
    for name, ep in s.endpoints.items():
        ep.name = name
    for name, mc in s.models.items():
        mc.name = name
    s.routines_home = routines_home
    s.libraries_home = routines_home.parent / "test-library"
    return s


def test_resolve_chain_follows_fallbacks_transitively(tmp_path):
    """R1504/R1492: a FLAT chain silently contradicted the config that declared it. `Astra high`
    declared `[Opus high]`, `Opus high` declared nothing, and both sat on the same cliproxy
    endpoint — so the chain was two models in one failure domain. One credential expiry on
    2026-09-14 killed eight routines at turn 0, and the same empty walk ends a run on a
    classifier refusal (engine/degrade walks this chain for both).

    The reachable set is now what a reader of config.yaml would expect, with no config edit."""
    reg = EndpointRegistry(_nested_server(tmp_path / "routines"))
    names = [ref.name for _, ref in reg.resolve_chain("astra")]
    assert names == ["astra", "opus", "glm-a", "glm-b"]
    # every member resolves, and the chain escapes the primary's endpoint — the point of it
    assert {ep.name for ep, _ in reg.resolve_chain("astra")} == {"proxy", "other"}
    # a cycle back to the primary terminates instead of looping
    assert [ref.name for _, ref in reg.resolve_chain("glm-b")] == ["glm-b", "astra", "opus",
                                                                   "glm-a"]


def test_resolve_chain_breadth_first_keeps_the_authors_order(tmp_path):
    """Breadth-first, so order still expresses intent: everything the primary itself named is
    tried before anything only a fallback named."""
    reg = EndpointRegistry(_nested_server(tmp_path / "routines"))
    # head declares [broken, opus]; broken declares [glm-a]. glm-a must come AFTER opus,
    # because head chose opus itself and only `broken` chose glm-a. The tail (glm-b, then
    # astra through glm-b) is what transitivity adds.
    assert [ref.name for _, ref in reg.resolve_chain("head")] == ["head", "opus", "glm-a",
                                                                  "glm-b", "astra"]


def test_resolve_chain_unresolvable_rung_does_not_truncate_the_ladder(tmp_path):
    """`broken` names a missing endpoint, so as a FALLBACK it is skipped — but what it
    declared is still followed. A bad rung costs one model, not every model below it.

    As a PRIMARY it still raises: a run must not quietly start with no model at all, and
    that refusal is `resolve`'s, not this walk's."""
    reg = EndpointRegistry(_nested_server(tmp_path / "routines"))
    names = [ref.name for _, ref in reg.resolve_chain("head")]
    assert "broken" not in names and "glm-a" in names   # skipped, but its declaration survived
    with pytest.raises(EndpointError):
        reg.resolve_chain("broken")


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


# ---- empty completions + classifier refusals (R5) --------------------------------------------

def test_empty_completions_fail_over_to_fallback(make_routine, monkeypatch):
    """Two consecutive EMPTY completions from one model engage the failover chain exactly
    like a hard EndpointError — same-model blind retries can never fix a broken model, so
    the turn moves on and the model cools. (A refusal-shaped stop never reaches this
    path — it advances the chain on the FIRST occurrence, see the refusal tests below.)"""
    from rsched.endpoints.base import Completion

    d = make_routine("emptyfo")
    server = _catalog_server(d.parent)
    eps = _wire(monkeypatch, server, {
        "epA": [Completion(text=""),
                Completion(text="")],
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="served by the backup model")]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert len(eps["epA"].calls) == 2       # exactly two empties, then the chain advances
    events, _ = read_events(run_dir / "transcript.jsonl")
    empties = [e for e in events if e["type"] == "error"
               and "empty completion (no content" in e["payload"].get("message", "")]
    assert len(empties) == 2
    assert "stop_reason=unreported" in empties[0]["payload"]["message"]
    switch = [e for e in events if e["type"] == "error" and e["payload"].get("failover")]
    assert switch and switch[0]["payload"]["failover"]["to"] == "backup"
    assert failover.is_cooling("epA", "m-a")
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert [t["usage"]["model"] for t in turns] == ["epB/m-b", "epB/m-b"]


def test_refusal_advances_chain_immediately(make_routine, monkeypatch):
    """R5: a classifier refusal (HTTP 200, stop_reason "refusal", stop_details naming the
    category) is NEVER retried against the same model — the FIRST refusal emits a
    refusal-marked transcript error and advances the fallback chain; the refused model
    cools (run-scoped: the registry is process-local)."""
    from rsched.endpoints.base import Completion

    d = make_routine("refusalfo")
    server = _catalog_server(d.parent)
    eps = _wire(monkeypatch, server, {
        "epA": [Completion(text="", stop_reason="refusal",
                           stop_details={"type": "refusal", "category": "cyber",
                                         "explanation": "declined by the classifier"})],
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="served by the backup model")]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert len(eps["epA"].calls) == 1       # ONE refusal — no same-model retry, ever
    events, _ = read_events(run_dir / "transcript.jsonl")
    refusals = [e for e in events if e["type"] == "error" and e["payload"].get("refusal")]
    assert len(refusals) == 1
    assert refusals[0]["payload"]["refusal"]["category"] == "cyber"
    assert refusals[0]["payload"]["refusal"]["explanation"] == "declined by the classifier"
    assert "category=cyber" in refusals[0]["payload"]["message"]
    switch = [e for e in events if e["type"] == "error" and e["payload"].get("failover")]
    assert switch and switch[0]["payload"]["failover"]["to"] == "backup"
    assert failover.is_cooling("epA", "m-a")
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert [t["usage"]["model"] for t in turns] == ["epB/m-b", "epB/m-b"]


def test_refusal_without_fallback_fails_run_honestly(make_routine, monkeypatch):
    """R5: no fallbacks configured (and no uncensored role) → the run dies with an HONEST
    error naming the refusal category — never "empty completion", never "failed to
    produce a valid action"."""
    from rsched.endpoints.base import Completion

    d = make_routine("refusalsolo")
    server = ServerConfig(
        endpoints={"epA": EndpointConfig(kind="openai", base_url="http://127.0.0.1:1/v1")},
        models={"solo": ModelConfig(endpoint="epA", model="m-a")},
        system_model="solo")
    server.endpoints["epA"].name = "epA"
    server.models["solo"].name = "solo"
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "test-library"
    _wire(monkeypatch, server, {
        "epA": [Completion(text="", stop_reason="refusal",
                           stop_details={"category": "bio"})]})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "failed"
    events, _ = read_events(run_dir / "transcript.jsonl")
    refusals = [e for e in events if e["type"] == "error" and e["payload"].get("refusal")]
    assert refusals and refusals[0]["payload"]["refusal"]["category"] == "bio"
    fin = next(e for e in events if e["type"] == "finish")
    assert "refused the turn" in fin["payload"]["summary"]
    assert "category=bio" in fin["payload"]["summary"]
    assert "no usable fallback" in fin["payload"]["summary"]
    assert "empty completion" not in fin["payload"]["summary"]
    assert "valid action" not in fin["payload"]["summary"]


def test_classifier_refusal_with_harness_never_hands_it_the_turn(make_routine, monkeypatch):
    """0.213.0 (operator order 2026-08-22): the uncensored role is a honeypot HARNESS —
    even when configured, a refused turn is never re-issued to it wholesale and nothing
    it says can serve a turn. The refusal is flagged (`refusal` transcript event; the
    scripted fixture answers the isolation subcall with "no isolation", so nothing is
    referred), the refused model cools like any hard failure, and the fallback chain
    serves the run."""
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
        "epA": [Completion(text="", stop_reason="refusal",
                           stop_details={"category": "cyber"})],
        "epB": [write_file("state/probe.txt", say="the backup model serves the turn"),
                finish(summary="served by the backup model")],
        "epU": []})
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert len(eps["epU"].calls) == 0              # the harness NEVER sees the turn
    assert len(eps["epA"].calls) == 1              # one refusal — no same-model retry
    assert failover.is_cooling("epA", "m-a")       # a refusal now always fails over
    events, _ = read_events(run_dir / "transcript.jsonl")
    flags = [e for e in events if e["type"] == "refusal"]
    assert len(flags) == 1 and flags[0]["payload"]["where"] == "loop"
    assert flags[0]["payload"]["referred"] is False        # nothing isolated → nothing sent
    turns = [e for e in events if e["type"] == "assistant_action"]
    assert all("referred" not in t for t in turns)         # the old stamp is retired
    assert turns[0]["usage"]["model"] == "epB/m-b"
    status_json = _json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status_json["referrals"] == 0


def test_chain_exhaustion_reaches_the_health_stream(make_routine, monkeypatch):
    """F491: a run whose whole chain is cooling still finishes — the retry tax is paid in
    wall-clock and tokens and recorded NOWHERE a health sweep can see. On 2026-09-16, 11 of
    13 fleet runs opened with "All credentials for model gpt-6-astra are cooling down" and
    every one finished `ok`, so every health signal read clean. The transcript names the
    switch per run; nothing aggregates it. This is the same class as `cache_read_degraded`
    and `model_window_corrected` — an engine-side fact emitted precisely because nothing
    else shows it.
    """
    import json as _json

    d = make_routine("exhausted-health")
    server = _catalog_server(d.parent)
    _wire(monkeypatch, server, {
        "epA": [EndpointError("epA is down")],
        "epB": [EndpointError("epB is down too")]})
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "failed"

    stream = d.parent / ".control" / "health-events.jsonl"
    lines = stream.read_text(encoding="utf-8").splitlines() if stream.exists() else []
    events = [e for e in (_json.loads(ln) for ln in lines if ln.strip())
              if e.get("event") == "model_chain_exhausted"]
    assert events, (
        "a chain exhaustion emitted no health event — the one signal that would make the "
        "fleet's cooldown tax visible to an audit sweep")
    ev = events[-1]
    assert ev["routine"] == "exhausted-health"
    # the two models are STRUCTURED fields, not prose: "which model is burning the fleet's
    # time, and since when" has to be answerable by a filter (the F422 lesson).
    assert ev["model"] == "prime"
    assert ev["last_model"] == "backup"
    assert "epB is down too" in ev["detail"]


def test_every_failover_reaches_the_health_stream(make_routine, monkeypatch):
    """A switch that the chain ABSORBS is the expensive-and-invisible case: the run finishes
    `ok`, so `model_chain_exhausted` never fires and nothing fleet-level records that the
    primary stopped serving. Ten days in September 2026 held 123 switches and one exhaustion
    — a dead proxy refresh token and a weekly quota moved 28 runs onto metered models at
    `effort: max`, and the operator found it by reading transcripts, days later.
    """
    import json as _json

    d = make_routine("failover-health")
    server = _catalog_server(d.parent)
    _wire(monkeypatch, server, {
        "epA": [EndpointError("epA: HTTP 429: usage_limit_reached")],
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="served by the backup model")]})
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"          # the chain absorbed it — nothing else would report this

    stream = d.parent / ".control" / "health-events.jsonl"
    lines = stream.read_text(encoding="utf-8").splitlines() if stream.exists() else []
    events = [e for e in (_json.loads(ln) for ln in lines if ln.strip())
              if e.get("event") == "model_failover"]
    assert events, "a mid-run model switch emitted no health event"
    ev = events[-1]
    assert ev["routine"] == "failover-health"
    # structured, so a sweep can ask "what is the fleet running on today" with a filter
    assert ev["model"] == "prime"              # the chain HEAD is the grouping key
    assert ev["from_model"] == "prime" and ev["to_model"] == "backup"
    assert ev["reason"] == "rate_limit"        # a quota, not an outage — the classes differ
