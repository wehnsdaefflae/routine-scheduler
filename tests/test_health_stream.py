"""The health stream's read side (readmodels.health_stream) and `/api/health/blocked`.

The six blocked-fleet events had fourteen writers and no reader inside the product: a
refused fire, a stopped chain and a capped trigger produce no run, so no other surface can
carry them. These tests pin the fold (one row per event+subject, newest first, windowed) and
the route, plus the per-routine budget/partial split the usage stream cannot make.
"""

import json
from datetime import UTC, datetime, timedelta

from rsched.config import ServerConfig
from rsched.health_events import log_health_event
from rsched.readmodels import memo
from rsched.readmodels.health_stream import (
    BLOCKED_EVENTS,
    MAX_WINDOW_DAYS,
    blocked_fleet,
    budget_endings,
    health_records,
)


def _ago(hours):
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _server(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    return server


def _write(server, rows):
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


def test_every_blocked_event_is_in_the_writers_vocabulary():
    """The set is a SUBSET of health_events.py's enum — a name that drifts out of the header
    docstring would make the console blind to that event exactly the way it was blind to all
    six.
    """
    from rsched import health_events

    documented = health_events.__doc__ or ""
    for name in BLOCKED_EVENTS:
        assert f'"{name}"' in documented, name


def test_missing_stream_is_empty_not_an_error(tmp_path):
    server = _server(tmp_path)
    assert health_records(server.routines_home) == []
    out = blocked_fleet(server)
    assert out["total"] == 0 and out["rows"] == []
    assert set(out["vocabulary"]) == set(BLOCKED_EVENTS)


def test_unparseable_lines_are_skipped(tmp_path):
    server = _server(tmp_path)
    control = server.routines_home / ".control"
    control.mkdir(parents=True)
    good = json.dumps({"event": "fire_refused", "routine": "a", "ts": _ago(1)})
    (control / "health-events.jsonl").write_text(
        f"{good}\nnot json\n\n[1,2]\n", encoding="utf-8")
    memo.reset()
    assert len(health_records(server.routines_home)) == 1


def test_rows_group_by_event_and_subject_newest_first(tmp_path):
    server = _server(tmp_path)
    _write(server, [
        {"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": _ago(30),
         "detail": "overrun"},
        {"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": _ago(2),
         "detail": "draining"},
        {"event": "trigger_capped", "routine": "beta", "run_id": "", "ts": _ago(1),
         "detail": "report trigger"},
        # a non-blocked event never enters the fold — a failure already has a run page
        {"event": "run_failed", "routine": "alpha", "run_id": "x", "ts": _ago(1)},
    ])
    out = blocked_fleet(server)
    assert out["total"] == 3
    assert [(r["event"], r["subject"], r["count"]) for r in out["rows"]] == [
        ("trigger_capped", "beta", 1), ("fire_refused", "alpha", 2)]
    alpha = out["rows"][1]
    assert alpha["detail"] == "draining"          # the NEWEST detail, not the first
    assert alpha["first_ts"] < alpha["last_ts"]
    assert alpha["means"] == BLOCKED_EVENTS["fire_refused"]


def test_window_excludes_older_events(tmp_path):
    server = _server(tmp_path)
    _write(server, [
        {"event": "lane_chain_stopped", "routine": "grp-1", "run_id": "lr-1",
         "ts": _ago(24 * 30), "detail": "old"},
        {"event": "lane_fire_refused", "routine": "grp-1", "run_id": "", "ts": _ago(5)},
    ])
    assert blocked_fleet(server, days=7)["total"] == 1
    assert blocked_fleet(server, days=MAX_WINDOW_DAYS)["total"] == 2


def test_budget_endings_split_per_routine(tmp_path):
    """Both land in the usage stream as `partial`; only the health stream says which budget
    forced one."""
    server = _server(tmp_path)
    _write(server, [
        {"event": "budget_exhausted", "routine": "alpha", "run_id": "a1", "ts": _ago(4),
         "detail": "turns 100/100"},
        {"event": "budget_exhausted", "routine": "alpha", "run_id": "a2", "ts": _ago(2),
         "detail": "tokens 1/1"},
        {"event": "run_partial", "routine": "alpha", "run_id": "a3", "ts": _ago(1)},
        {"event": "budget_exhausted", "routine": "beta", "run_id": "b1", "ts": _ago(1)},
    ])
    out = budget_endings(server, "alpha")
    assert out["budget_exhausted"] == 2 and out["run_partial"] == 1
    assert out["last_detail"] == ""                 # the newest ending is the run_partial
    assert budget_endings(server, "gamma")["budget_exhausted"] == 0


def test_fold_reflects_a_new_append(tmp_path):
    """The memo keys on the file's stat fingerprint, so an appended event shows up without
    a process restart — this rides a bus-event refresh path."""
    server = _server(tmp_path)
    _write(server, [])
    assert blocked_fleet(server)["total"] == 0
    log_health_event(server.routines_home, "scheduler_tick_error", routine="", run_id="",
                     detail="boom")
    out = blocked_fleet(server)
    assert out["total"] == 1 and out["rows"][0]["subject"] == ""


def test_route_serves_the_blocked_fold(api_client):
    c, tmp = api_client
    control = tmp / "routines" / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        json.dumps({"event": "lane_chain_member_skipped", "routine": "ghost",
                    "run_id": "lr-9", "ts": _ago(3), "detail": "lane grp-1 continued"}) + "\n",
        encoding="utf-8")
    memo.reset()

    r = c.get("/api/health/blocked")
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 7 and body["total"] == 1
    row = body["rows"][0]
    assert row["event"] == "lane_chain_member_skipped" and row["subject"] == "ghost"
    assert row["detail"] == "lane grp-1 continued"
    assert body["vocabulary"][row["event"]] == row["means"]

    assert c.get("/api/health/blocked?days=0").status_code == 422
    assert c.get(f"/api/health/blocked?days={MAX_WINDOW_DAYS + 1}").status_code == 422
