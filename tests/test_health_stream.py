"""The health stream's read side (readmodels.health_stream) and `/api/health/blocked`.

The blocked-fleet events had fourteen writers and no reader inside the product: a
refused fire, a stopped chain and a capped trigger produce no run; a commit that did not land
happens after a run's finish or outside any run. No other surface can carry them. These tests pin the fold (one row per event+subject, newest first, windowed) and
the route, plus the per-routine budget/partial split the usage stream cannot make.
"""

import json
from datetime import UTC, datetime, timedelta, timezone

from helpers import tmp_server
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
    server = tmp_server(tmp_path, create=False)
    assert health_records(server.routines_home) == []
    out = blocked_fleet(server)
    assert out["total"] == 0 and out["rows"] == []
    assert set(out["vocabulary"]) == set(BLOCKED_EVENTS)


def test_unparseable_lines_are_skipped(tmp_path):
    server = tmp_server(tmp_path, create=False)
    control = server.routines_home / ".control"
    control.mkdir(parents=True)
    good = json.dumps({"event": "fire_refused", "routine": "a", "ts": _ago(1)})
    (control / "health-events.jsonl").write_text(
        f"{good}\nnot json\n\n[1,2]\n", encoding="utf-8")
    memo.reset()
    assert len(health_records(server.routines_home)) == 1


def test_rows_group_by_event_and_subject_newest_first(tmp_path):
    server = tmp_server(tmp_path, create=False)
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
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "lane_chain_stopped", "routine": "grp-1", "run_id": "lr-1",
         "ts": _ago(24 * 30), "detail": "old"},
        {"event": "lane_fire_refused", "routine": "grp-1", "run_id": "", "ts": _ago(5)},
    ])
    assert blocked_fleet(server, days=7)["total"] == 1
    assert blocked_fleet(server, days=MAX_WINDOW_DAYS)["total"] == 2


def _at(delta, offset_hours):
    """A stamp `delta` from now, written the way `ids.now_iso` writes it on a host whose zone
    is `offset_hours` from UTC — the suite runs in UTC, the instance in Europe/Berlin."""
    zone = timezone(timedelta(hours=offset_hours))
    return (datetime.now(UTC) + delta).astimezone(zone).isoformat(timespec="seconds")


def test_the_window_edge_is_an_instant_whatever_offset_wrote_the_stamp(tmp_path):
    """`now_iso` writes LOCAL time with its offset, and the cutoff was a UTC string: compared as
    text, the window's edge moved by the host's offset. An event an hour too old, written on a
    UTC+2 host, read as inside the week; one an hour inside it, written at UTC-5, as outside."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "fire_refused", "routine": "too-old", "run_id": "",
         "ts": _at(-timedelta(days=7, hours=1), 2)},
        {"event": "fire_refused", "routine": "inside", "run_id": "",
         "ts": _at(-timedelta(days=6, hours=23), -5)},
    ])
    out = blocked_fleet(server, days=7)
    assert [r["subject"] for r in out["rows"]] == ["inside"]


def test_the_newest_detail_is_the_newest_instant_not_the_largest_string(tmp_path):
    """Stamps written under different offsets (a DST change, a host moved between zones) order
    by the instant they name. As text, the older one — written at the larger offset, so an
    hour LATER on the wall clock — won."""
    server = tmp_server(tmp_path, create=False)
    older, newer = _at(-timedelta(hours=3), 3), _at(-timedelta(hours=1), 0)
    _write(server, [
        {"event": "budget_exhausted", "routine": "alpha", "run_id": "a1", "ts": older,
         "detail": "turns 100/100"},
        {"event": "budget_exhausted", "routine": "alpha", "run_id": "a2", "ts": newer,
         "detail": "tokens 1/1"},
        {"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": older,
         "detail": "overrun"},
        {"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": newer,
         "detail": "draining"},
    ])
    assert budget_endings(server, "alpha")["last_detail"] == "tokens 1/1"
    row = blocked_fleet(server)["rows"][0]
    assert (row["detail"], row["first_ts"], row["last_ts"]) == ("draining", older, newer)


def test_the_window_moves_with_the_clock_while_the_file_stands_still(tmp_path, monkeypatch):
    """A fold memoized on the file's fingerprint alone answered for the moment it was first
    computed until the next append — on a quiet instance, for days."""
    from rsched.readmodels import health_stream

    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "trigger_capped", "routine": "beta", "run_id": "",
                     "ts": _at(-timedelta(days=6), 2)},
                    {"event": "budget_exhausted", "routine": "beta", "run_id": "b1",
                     "ts": _at(-timedelta(days=6), 2)}])
    assert blocked_fleet(server)["total"] == 1
    assert budget_endings(server, "beta")["budget_exhausted"] == 1

    class TwoDaysLater(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr(health_stream, "datetime", TwoDaysLater)
    assert blocked_fleet(server)["total"] == 0
    assert budget_endings(server, "beta")["budget_exhausted"] == 0


def test_budget_endings_split_per_routine(tmp_path):
    """Both land in the usage stream as `partial`; only the health stream says which budget
    forced one."""
    server = tmp_server(tmp_path, create=False)
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
    server = tmp_server(tmp_path, create=False)
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
