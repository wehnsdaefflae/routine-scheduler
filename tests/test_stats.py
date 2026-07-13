"""Usage statistics aggregation across the routines + conversations homes."""

import yaml

from rsched.config import ServerConfig
from rsched.paths import atomic_write_json
from rsched.stats import aggregate


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.conversations_home = tmp_path / "conversations"
    return s


def _mk_routine(home, slug, *, endpoint="claude", model="opus"):
    d = home / slug
    (d / "state").mkdir(parents=True)
    cfg = {
        "name": f"Test {slug}", "slug": slug, "enabled": True,
        "description": "A test routine.",
        "schedule": {"cron": "0 7 * * 1", "tz": "Etc/UTC", "catchup": "skip"},
        "workflow": {"library_slug": "test-flow", "library_commit": "abc123"},
        "models": {"main": {"endpoint": endpoint, "model": model}},
    }
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return d


def _mk_run(d, ts, state, *, tin=0, tout=0, cost=None, elapsed_s=0, model=None):
    run_dir = d / "runs" / ts
    run_dir.mkdir(parents=True)
    usage = {"in": tin, "out": tout}
    if cost is not None:
        usage["cost"] = cost
    st = {"run_id": f"{d.name}:{ts}", "state": state, "turn": 3,
          "usage": usage, "elapsed_s": elapsed_s}
    if model:
        st["model"] = model
    atomic_write_json(run_dir / "status.json", st)
    return run_dir


def test_aggregate_rolls_up_every_slice(tmp_path):
    home = tmp_path / "routines"
    conv = tmp_path / "conversations"
    a = _mk_routine(home, "alpha", endpoint="claude", model="opus")
    b = _mk_routine(home, "beta", endpoint="openrouter", model="glm-5.2")
    c = _mk_routine(conv, "chat1", endpoint="claude", model="opus")

    _mk_run(a, "20260712-070000", "finished", tin=100, tout=40, elapsed_s=60, model="opus")
    _mk_run(a, "20260713-070000", "failed", tin=50, tout=10, elapsed_s=30, model="opus")
    _mk_run(b, "20260713-080000", "finished", tin=200, tout=60, cost=0.5, elapsed_s=90,
            model="glm-5.2")
    _mk_run(c, "20260713-090000", "finished", tin=10, tout=5, elapsed_s=15, model="opus")

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
    # by_model
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
    assert list(agg["by_routine"])[0] == "beta"  # most tokens


def test_aggregate_empty_homes(tmp_path):
    agg = aggregate(_server(tmp_path))
    assert agg["totals"]["runs"] == 0
    assert agg["totals"]["success_rate"] is None
    assert agg["by_routine"] == {}
