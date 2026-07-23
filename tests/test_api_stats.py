"""/api/stats — the usage roll-up over the wire, in the exact shape static/views/stats.js
consumes: totals for the cards, the by_* slices for the tables, and per-run rows for the
configurable charts. Aggregation math itself lives in test_stats.py; this pins the route
wiring + serialized surface."""

from conftest import mk_run


def _mk_run(routine_dir, ts, state, *, tin, tout, cost=None, elapsed_s=0, model=None):
    usage = {"in": tin, "out": tout, **({"cost": cost} if cost is not None else {})}
    mk_run(routine_dir, ts, state, usage=usage, elapsed_s=elapsed_s, model=model or None)


def test_stats_route_serves_the_stats_tab_shape(api_client, make_routine):
    c, _tmp = api_client
    d = make_routine(slug="alpha")
    _mk_run(d, "20260712-070000", "finished", tin=100, tout=40, cost=0.25,
            elapsed_s=60, model="dummy/m")           # a modern run records endpoint/model
    _mk_run(d, "20260713-070000", "failed", tin=50, tout=10, elapsed_s=30)

    r = c.get("/api/stats")
    assert r.status_code == 200
    agg = r.json()

    # the durable monthly series rides the same payload (empty here — no usage stream)
    assert agg["monthly"] == {"months": [], "by_routine": {}}

    # totals → the summary cards
    t = agg["totals"]
    assert t["runs"] == 2 and t["tokens_in"] == 150 and t["tokens_out"] == 50
    assert t["cost"] == 0.25 and t["elapsed_s"] == 90
    assert t["routines"] == 1 and t["conversations"] == 0
    assert t["success_rate"] == 0.5                   # 1 finished / 2 graded

    # slice tables
    row = agg["by_routine"]["alpha"]
    for col in ("runs", "tokens_in", "tokens_out", "cost", "elapsed_s"):
        assert col in row, col
    assert row["kind"] == "routine"
    # the run without a recorded model attributes via the system model (catalog m → dummy)
    assert agg["by_endpoint"]["dummy"]["runs"] == 2
    assert agg["by_model"]["m"]["runs"] == 2
    assert agg["by_state"] == {"finished": 1, "failed": 1}
    assert set(agg["by_day"]) == {"2026-07-12", "2026-07-13"}
    assert agg["by_kind"]["routine"]["runs"] == 2

    # per-run rows → the configurable charts (bucketed client-side by dimension × metric)
    runs = agg["runs"]
    assert [row_["day"] for row_ in runs] == ["2026-07-12", "2026-07-13"]   # day-sorted
    for col in ("day", "routine", "kind", "state", "model", "endpoint",
                "tokens_in", "tokens_out", "cost", "elapsed_s"):
        assert col in runs[0], col


def test_stats_route_carries_util_stats(api_client, make_routine):
    """The per-util section rides the same payload (aggregation math in
    test_util_stats.py; this pins the wire shape stats.js consumes)."""
    c, tmp = api_client
    make_routine(slug="alpha")
    control = tmp / "routines" / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "workflow-usage.jsonl").write_text(
        '{"run_id": "alpha:x", "ts": "2026-07-15T10:00:00+00:00", '
        '"utils": {"fetch": {"ok": 2, "denied": 1}}}\n', encoding="utf-8")
    u = c.get("/api/stats").json()["utils"]
    row = next(r_ for r_ in u["utils"] if r_["name"] == "fetch")
    assert row["executed"] == 2 and row["ok"] == 2 and row["denied"] == 1
    assert row["in_library"] is False                 # hermetic library holds no utils
    for col in ("created", "revised", "first_executed", "last_executed",
                "usage_error", "rejected", "missing"):
        assert col in row, col


def test_stats_route_requires_auth(api_client):
    c, _tmp = api_client
    assert c.get("/api/stats", headers={"Authorization": ""}).status_code == 401
