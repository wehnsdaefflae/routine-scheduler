"""Usage statistics aggregation across the routines + conversations homes."""

import json

import yaml

from conftest import mk_run
from rsched.config import EndpointConfig, ModelConfig, ServerConfig
from rsched.readmodels.stats import aggregate, monthly_spend


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.conversations_home = tmp_path / "conversations"
    # endpoints + a catalog the routines' model NAMES resolve against (stats resolves a
    # routine's main model to attribute legacy runs whose status.json lacks the model)
    s.endpoints = {"claude": EndpointConfig(name="claude", kind="anthropic"),
                   "openrouter": EndpointConfig(name="openrouter", kind="openai", base_url="http://x")}
    s.models = {
        "opus": ModelConfig(name="opus", endpoint="claude", model="opus"),
        "glm": ModelConfig(name="glm", endpoint="openrouter", model="glm-5.2"),
        "glm52": ModelConfig(name="glm52", endpoint="openrouter", model="z-ai/glm-5.2"),
    }
    return s


def _mk_routine(home, slug, *, model_name="opus"):
    d = home / slug
    (d / "state").mkdir(parents=True)
    cfg = {
        "name": f"Test {slug}", "slug": slug, "enabled": True,
        "description": "A test routine.",
        "schedule": {"cron": "0 7 * * 1", "tz": "Etc/UTC", "catchup": "skip"},
        "workflow": {"library_slug": "test-flow", "library_commit": "abc123"},
    }
    if model_name:  # None -> no models: block (the engine falls to system_model)
        cfg["models"] = {"main": model_name}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return d


def _mk_run(d, ts, state, *, tin=0, tout=0, cost=None, elapsed_s=0, model=None):
    usage = {"in": tin, "out": tout, **({"cost": cost} if cost is not None else {})}
    return mk_run(d, ts, state, usage=usage, elapsed_s=elapsed_s, model=model or None)


def test_aggregate_rolls_up_every_slice(tmp_path):
    home = tmp_path / "routines"
    conv = tmp_path / "conversations"
    a = _mk_routine(home, "alpha", model_name="opus")
    b = _mk_routine(home, "beta", model_name="glm")
    c = _mk_routine(conv, "chat1", model_name="opus")

    # status.json records the resolved "<endpoint>/<model>"; one bare legacy value stays
    _mk_run(a, "20260712-070000", "finished", tin=100, tout=40, elapsed_s=60,
            model="claude/opus")
    _mk_run(a, "20260713-070000", "failed", tin=50, tout=10, elapsed_s=30, model="opus")
    _mk_run(b, "20260713-080000", "finished", tin=200, tout=60, cost=0.5, elapsed_s=90,
            model="openrouter/glm-5.2")
    _mk_run(c, "20260713-090000", "finished", tin=10, tout=5, elapsed_s=15,
            model="claude/opus")

    agg = aggregate(_server(tmp_path))

    t = agg["totals"]
    assert t["runs"] == 4
    assert t["tokens_in"] == 360 and t["tokens_out"] == 115
    assert t["cost"] == 0.5
    assert t["elapsed_s"] == 195
    assert t["routines"] == 2 and t["conversations"] == 1
    # 3 finished, 1 failed -> 3/4
    assert t["success_rate"] == 0.75

    # by_endpoint attributes b's cost to openrouter; claude covers alpha + chat1
    assert agg["by_endpoint"]["openrouter"]["cost"] == 0.5
    assert agg["by_endpoint"]["claude"]["runs"] == 3
    # by_model buckets on the bare model id (endpoint prefix split off)
    assert agg["by_model"]["opus"]["runs"] == 3
    assert agg["by_model"]["glm-5.2"]["tokens_in"] == 200
    # by_kind separates routines vs conversations
    assert agg["by_kind"]["routine"]["runs"] == 3
    assert agg["by_kind"]["conversation"]["runs"] == 1
    # by_day buckets on the run-id date
    assert agg["by_day"]["2026-07-13"]["runs"] == 3
    assert agg["by_day"]["2026-07-12"]["runs"] == 1
    # by_state raw counts
    assert agg["by_state"]["finished"] == 3 and agg["by_state"]["failed"] == 1
    # by_routine carries endpoint/model attribution, sorted by tokens desc
    assert agg["by_routine"]["beta"]["endpoint"] == "openrouter"
    assert next(iter(agg["by_routine"])) == "beta"  # most tokens


def test_aggregate_no_models_block_falls_back_to_system_model(tmp_path):
    """A routine without a models: block runs on the server's system_model (the engine's
    for_model fallback) — its runs must not land in the "unknown" buckets."""
    home = tmp_path / "routines"
    d = _mk_routine(home, "improver", model_name=None)
    # a modern run records the resolved model; a legacy run recorded nothing
    _mk_run(d, "20260713-070000", "finished", tin=100, tout=20, cost=0.55,
            model="openrouter/z-ai/glm-5.2")
    _mk_run(d, "20260712-070000", "finished", tin=10, tout=5)

    server = _server(tmp_path)
    server.system_model = "glm52"   # catalog name → openrouter / z-ai/glm-5.2
    agg = aggregate(server)

    assert "unknown" not in agg["by_endpoint"] and "unknown" not in agg["by_model"]
    assert agg["by_endpoint"]["openrouter"]["runs"] == 2
    # a model id containing "/" splits on the FIRST slash only
    assert agg["by_model"]["z-ai/glm-5.2"]["runs"] == 2
    assert agg["by_model"]["z-ai/glm-5.2"]["cost"] == 0.55
    r = agg["by_routine"]["improver"]
    assert r["endpoint"] == "openrouter" and r["model"] == "z-ai/glm-5.2"


def test_aggregate_recorded_model_beats_routine_config(tmp_path):
    """status.json's resolved "<endpoint>/<model>" wins over routine.yaml — a mid-run
    switch_model attributes to the model that actually served the run."""
    home = tmp_path / "routines"
    d = _mk_routine(home, "switcher", model_name="opus")
    _mk_run(d, "20260713-070000", "finished", tin=10, tout=5,
            model="openrouter/z-ai/glm-5.2")

    agg = aggregate(_server(tmp_path))

    assert agg["by_endpoint"]["openrouter"]["runs"] == 1
    assert agg["by_model"]["z-ai/glm-5.2"]["runs"] == 1
    assert "claude" not in agg["by_endpoint"]


def test_aggregate_unknown_only_when_unattributable(tmp_path):
    """No models: block, no system_model, no recorded model — the one genuinely
    unattributable (legacy) case keeps the "unknown" buckets."""
    home = tmp_path / "routines"
    d = _mk_routine(home, "legacy", model_name=None)
    _mk_run(d, "20260712-070000", "finished", tin=1, tout=1)

    agg = aggregate(_server(tmp_path))

    assert agg["by_endpoint"]["unknown"]["runs"] == 1
    assert agg["by_model"]["unknown"]["runs"] == 1
    r = agg["by_routine"]["legacy"]
    assert r["endpoint"] == "unknown" and r["model"] == "unknown"


def test_aggregate_empty_homes(tmp_path):
    agg = aggregate(_server(tmp_path))
    assert agg["totals"]["runs"] == 0
    assert agg["totals"]["success_rate"] is None
    assert agg["by_routine"] == {}


def test_monthly_spend_reads_the_durable_stream(tmp_path):
    """Monthly per-routine spend comes from workflow-usage.jsonl (survives run retention):
    depth-0 entries only (a parent's usage already folds its children in), background-task
    slugs attributed to their owner, malformed/dateless lines skipped, routines ordered by
    latest-month tokens."""
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    ctrl = s.routines_home / ".control"
    ctrl.mkdir(parents=True)
    entries = [
        {"ts": "2026-06-20T10:00:00+00:00", "routine": "alpha", "depth": 0,
         "tokens": 1000, "cost": 0.5},
        {"ts": "2026-07-01T10:00:00+00:00", "routine": "alpha", "depth": 0,
         "tokens": 3000, "cost": 1.5},
        {"ts": "2026-07-02T10:00:00+00:00", "routine": "alpha", "depth": 1,
         "tokens": 999_999, "cost": 9.0},                      # child — already in the parent
        {"ts": "2026-07-03T11:00:00+00:00", "routine": "bg-chat-1a2b3c4d", "depth": 0,
         "tokens": 500},                                       # detached task → owner "chat"
        {"ts": "", "routine": "x", "depth": 0, "tokens": 5},   # dateless → skipped
    ]
    with (ctrl / "workflow-usage.jsonl").open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
        fh.write("not json\n")
    m = monthly_spend(s)
    assert m["months"] == ["2026-06", "2026-07"]
    assert m["by_routine"]["alpha"]["2026-07"] == {"runs": 1, "tokens": 3000, "cost": 1.5,
                                                   "referrals": 0}
    assert m["by_routine"]["alpha"]["2026-06"]["tokens"] == 1000
    assert m["by_routine"]["chat"]["2026-07"] == {"runs": 1, "tokens": 500, "cost": 0.0,
                                                  "referrals": 0}
    assert list(m["by_routine"]) == ["alpha", "chat"]          # latest-month tokens, desc


def test_monthly_spend_without_stream(tmp_path):
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    assert monthly_spend(s) == {"months": [], "by_routine": {}}
