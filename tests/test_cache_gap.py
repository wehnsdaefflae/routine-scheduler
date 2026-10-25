"""The between-runs prompt-cache gap read model (rsched.readmodels.cache_gap) — F507/D163.

What a turn-1 usage row means (cold / warm / no cache traffic at all), the premium priced from
the engine's own recorded price table, and the route wiring that puts it on the Stats tab.
"""

import json

from helpers import stats_server
from rsched.readmodels.cache_gap import cache_gap

# the rates this instance actually records for claude-proxy|claude-opus-5 on 2026-10-24:
# in, out, cache_read, cache_write (USD per million tokens)
RATES = [5.0, 25.0, 0.5, 6.25]


def _prices(server, key="proxy|m", rates=RATES):
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "model-prices.json").write_text(
        json.dumps({"fetched": "2026-10-24T19:01:24+00:00", "rows": {key: {"rates": rates}}}),
        encoding="utf-8")


def _fire(server, slug, ts, usage, *, lead=0):
    """One retained run dir whose transcript's head carries `usage` on its first metered turn."""
    d = server.routines_home / slug / "runs" / ts
    d.mkdir(parents=True, exist_ok=True)
    events = [{"type": "boot", "payload": {}} for _ in range(lead)]
    events.append({"ts": f"{ts[:4]}-01-01T00:00:00+00:00", "type": "assistant_action",
                   "payload": {"kind": "read_file"}, "turn": 1, "usage": usage})
    events.append({"ts": f"{ts[:4]}-01-01T00:01:00+00:00", "type": "assistant_action",
                   "payload": {"kind": "finish"}, "turn": 2,
                   "usage": {"in": 2, "out": 9, "cached_in": 99999, "model": "proxy/m"}})
    (d / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_cold_fire_premium_is_the_write_minus_read_rate(tmp_path):
    """A turn-1 row with cache writes and NO cache reads is a cold start: the whole prefix was
    re-written because the provider's cache expired between fires. Its premium is the write
    tokens at the gap between the two recorded rates — 53992 x (6.25-0.5)/1e6 = 0.310454.
    """
    server = stats_server(tmp_path)
    _prices(server)
    _fire(server, "alpha", "20261025-140002",
          {"in": 2, "out": 258, "cache_write": 53992, "notional": 0.34391, "model": "proxy/m"})

    out = cache_gap(server)
    row = out["by_routine"]["alpha"]
    assert (row["fires"], row["cold"], row["warm"], row["nocache"]) == (1, 1, 0, 0)
    assert row["cold_tokens"] == 53992
    assert row["premium_usd"] == 0.310454
    assert row["per_cold_fire"] == 0.310454
    assert row["last_cold"] == "20261025-140002"
    assert row["warm_read_share"] is None        # no warm fire to read a share from
    assert out["totals"]["premium_usd"] == 0.310454
    assert out["rows"] == 1


def test_warm_and_uncached_fires_are_not_confused(tmp_path):
    """Three distinct turn-1 states. A row WITH cache reads is warm and costs no premium; a row
    with no cache traffic at all is counted apart (absence is not a hit) and never priced.
    """
    server = stats_server(tmp_path)
    _prices(server)
    _fire(server, "warm", "20261020-100000",
          {"in": 2, "out": 30, "cached_in": 40000, "cache_write": 8000, "model": "proxy/m"})
    _fire(server, "none", "20261020-110000", {"in": 9000, "out": 30, "model": "proxy/m"})

    out = cache_gap(server)
    warm = out["by_routine"]["warm"]
    assert (warm["cold"], warm["warm"], warm["nocache"]) == (0, 1, 0)
    assert warm["premium_usd"] == 0.0
    assert warm["warm_read_tokens"] == 40000 and warm["warm_write_tokens"] == 8000
    assert warm["warm_read_share"] == 0.8333
    bare = out["by_routine"]["none"]
    assert (bare["cold"], bare["warm"], bare["nocache"]) == (0, 0, 1)
    assert bare["premium_usd"] == 0.0
    assert out["totals"]["cold"] == 0


def test_unpriced_model_is_counted_never_estimated(tmp_path):
    """A cold fire whose model the recorded table does not price is counted as unpriced and adds
    nothing to the premium — the prices.py rule: what is not published is not estimated.
    """
    server = stats_server(tmp_path)
    _prices(server, key="other|x")
    _fire(server, "alpha", "20261025-140002",
          {"in": 2, "out": 258, "cache_write": 53992, "model": "proxy/m"})

    row = cache_gap(server)["by_routine"]["alpha"]
    assert (row["cold"], row["unpriced"], row["premium_usd"]) == (1, 1, 0.0)


def test_routines_are_ranked_by_premium(tmp_path):
    """The operator's question is which routine costs the most, so rows come back ordered by
    premium descending — and a run with no metered turn at all is skipped, not counted as a fire.
    """
    server = stats_server(tmp_path)
    _prices(server)
    _fire(server, "small", "20261025-010000",
          {"in": 2, "out": 10, "cache_write": 10000, "model": "proxy/m"})
    _fire(server, "big", "20261025-020000",
          {"in": 2, "out": 10, "cache_write": 600000, "model": "proxy/m"})
    empty = server.routines_home / "quiet" / "runs" / "20261025-030000"
    empty.mkdir(parents=True)
    (empty / "transcript.jsonl").write_text('{"type": "boot", "payload": {}}\n',
                                            encoding="utf-8")

    out = cache_gap(server)
    assert list(out["by_routine"]) == ["big", "small"]
    assert "quiet" not in out["by_routine"]
    assert out["totals"]["fires"] == 2


def test_first_metered_turn_is_found_past_leading_lines(tmp_path):
    """A transcript may open with unmetered lines; the first row CARRYING usage is turn 1."""
    server = stats_server(tmp_path)
    _prices(server)
    _fire(server, "alpha", "20261025-140002",
          {"in": 2, "out": 10, "cache_write": 20000, "model": "proxy/m"}, lead=3)

    row = cache_gap(server)["by_routine"]["alpha"]
    assert (row["cold"], row["cold_tokens"]) == (1, 20000)


def test_stats_route_carries_the_cache_gap(api_client, make_routine):
    """The Stats tab's one payload grows the cost view — the seam the operator asked for:
    extend /api/stats, do not build a surface beside it.
    """
    c, tmp = api_client
    make_routine(slug="alpha")
    control = tmp / "routines" / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "model-prices.json").write_text(
        json.dumps({"rows": {"proxy|m": {"rates": RATES}}}), encoding="utf-8")
    run = tmp / "routines" / "alpha" / "runs" / "20261025-140002"
    run.mkdir(parents=True, exist_ok=True)
    (run / "transcript.jsonl").write_text(json.dumps(
        {"type": "assistant_action", "payload": {"kind": "read_file"}, "turn": 1,
         "usage": {"in": 2, "out": 258, "cache_write": 53992, "model": "proxy/m"}}) + "\n",
        encoding="utf-8")

    r = c.get("/api/stats")
    assert r.status_code == 200
    gap = r.json()["cache_gap"]
    assert gap["by_routine"]["alpha"]["cold"] == 1
    assert gap["by_routine"]["alpha"]["premium_usd"] == 0.310454
