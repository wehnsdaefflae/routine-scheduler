"""The latest `cost_trend_degraded` finding per routine, read for the Stats page (decision #9)."""

import json
from datetime import UTC, datetime, timedelta

from conftest import authed_client, make_test_server
from helpers import tmp_server
from rsched.readmodels import cost_regressions, memo


def _ts(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _finding(routine, days, scope="routine", **over):
    return {"ts": _ts(days), "event": "cost_trend_degraded", "routine": routine,
            "run_id": f"{routine or 'x'}:r", "detail": f"{scope} sentence · reading",
            "scope": scope, "peers_compared": 4, "peers_degraded": 1, "fleet_share": 0.25,
            "window": 5, "turns_median_before": 20, "turns_median_after": 41,
            "volume_median_before": 1.0e6, "volume_median_after": 2.5e6,
            "fail_rate_before": 0.0, "fail_rate_after": 0.2, **over}


def _stream(server, rows) -> None:
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


def test_one_row_per_subject_the_newest_with_its_attribution(tmp_path):
    server = tmp_server(tmp_path)
    _stream(server, [
        _finding("alpha", 9, detail="older"),
        _finding("alpha", 2, detail="newer"),
        _finding("beta", 1, scope="unattributable", peers_compared=1, peers_degraded=0),
        {"ts": _ts(1), "event": "fire_refused", "routine": "alpha", "run_id": "", "detail": "x"},
    ])

    rows = cost_regressions.regressions(server)["rows"]

    assert [r["subject"] for r in rows] == ["beta", "alpha"], "newest first, one per routine"
    alpha = rows[1]
    assert alpha["detail"] == "newer" and alpha["slug"] == "alpha"
    assert alpha["scope"] == "routine" and alpha["peers_compared"] == 4
    assert (alpha["turns_median_before"], alpha["turns_median_after"]) == (20, 41)
    assert rows[0]["scope"] == "unattributable"


def test_a_host_finding_names_no_routine_and_links_where_it_was_detected(tmp_path):
    """`trend_attribution.event_payload`: a host row carries NO routine, by design — the link
    goes to the routine whose run read it."""
    server = tmp_server(tmp_path)
    _stream(server, [_finding("", 1, scope="host", detected_on="gamma")])

    [row] = cost_regressions.regressions(server)["rows"]
    assert row["subject"] == "" and row["slug"] == "gamma" and row["scope"] == "host"


def test_the_window_is_cut_at_every_call(tmp_path):
    server = tmp_server(tmp_path)
    _stream(server, [_finding("old", 40), _finding("recent", 3)])

    assert [r["subject"] for r in cost_regressions.regressions(server)["rows"]] == ["recent"]
    assert [r["subject"] for r in cost_regressions.regressions(server, days=60)["rows"]] == [
        "recent", "old"]


def test_an_empty_stream_is_an_empty_reading(tmp_path):
    server = tmp_server(tmp_path)
    assert cost_regressions.regressions(server) == {"window_days": 30, "rows": []}


def test_the_route_serves_it(tmp_path):
    server = tmp_server(tmp_path)
    _stream(server, [_finding("alpha", 1)])
    with authed_client(make_test_server(tmp_path)) as client:
        got = client.get("/api/health/cost-regressions?days=7")
    assert got.status_code == 200
    assert [r["subject"] for r in got.json()["rows"]] == ["alpha"]
