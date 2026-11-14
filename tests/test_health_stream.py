"""The health stream's read side (readmodels.health_stream) and `/api/health/blocked`.

The blocked-fleet events had fourteen writers and no reader inside the product: a
refused fire, a stopped chain and a capped trigger produce no run; a commit that did not land
happens after a run's finish or outside any run. No other surface can carry them. These tests pin the fold (one row per event+subject, newest first, windowed) and
the route, plus the per-routine budget/partial split the usage stream cannot make.
"""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from helpers import tmp_server
from rsched.health_events import log_health_event
from rsched.readmodels import memo
from rsched.readmodels.health_reads import budget_endings
from rsched.readmodels.health_stream import (
    BLOCKED_EVENTS,
    MAX_WINDOW_DAYS,
    blocked_fleet,
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
    from rsched import health_vocab

    for name in BLOCKED_EVENTS:
        assert name in health_vocab.EVENTS, name


def test_a_finish_gate_that_did_not_run_reaches_the_blocked_fold(tmp_path):
    """F678. A finish gate that RAISED did not judge the ending the run authored, so nothing
    verified what that run claimed — work that was due and did not happen. Before this it was
    visible only in the raw stream: the run page shows a finished run, because it finished."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "finish_gate_failed", "routine": "uir",
                     "run_id": "uir:20261016-120000", "ts": _ago(1),
                     "detail": "AttributeError in a guard"}])
    out = blocked_fleet(server)
    assert out["total"] == 1
    assert out["rows"][0]["event"] == "finish_gate_failed"
    assert out["rows"][0]["subject"] == "uir"
    assert "unchecked" in out["rows"][0]["means"]


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


# --- R3418: a standing condition's magnitude is its SPAN, never a row count ----------------


def test_a_standing_condition_reports_its_span_not_a_row_count(tmp_path):
    """`backup_stale` is filed at most once a day while the gap holds (`daemon/backup_watch`'s
    own latch), so its row count measured how many days the DAEMON happened to be up, not how
    bad the gap was — an instance that restarts often reported a smaller backup gap. The row
    now says how long the condition has stood, and carries no count to be read as facts.
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(24 * 5),
                     "detail": "no completed snapshot since 2026-11-04"},
                    {"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(24 * 4),
                     "detail": "no completed snapshot since 2026-11-04"},
                    {"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(1),
                     "detail": "no completed snapshot since 2026-11-04"}])
    [row] = blocked_fleet(server)["rows"]
    assert row["kind"] == "standing"
    assert "count" not in row, "three filings of ONE gap are not three facts"
    assert 118 <= row["standing_hours"] <= 120      # first seen 5 days ago, last an hour ago
    assert row["first_ts"] < row["last_ts"]


def test_an_incident_keeps_its_count_and_gains_no_span(tmp_path):
    """Doctrine 2's case is untouched: an incident that happened twice is two facts, and its
    magnitude stays the count — nothing about the twelve incident events changes here.
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": _ago(30),
                     "detail": "overrun"},
                    {"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": _ago(2),
                     "detail": "draining"}])
    [row] = blocked_fleet(server)["rows"]
    assert row["kind"] == "incident" and row["count"] == 2
    assert "standing_hours" not in row, "an incident has no span to read"


def test_the_payload_cannot_be_summed_across_the_two_units(tmp_path):
    """The defect was ONE `total` that added occurrences to days-the-daemon-was-up: a 19-night
    backup gap read as 19 beside `fire_refused: 19` — nineteen distinct refusals. The key is
    GONE rather than redefined, because a redefined key is read wrongly in silence, and the two
    figures that replace it name their own units. `sum(r["count"] …)` over a mixed payload now
    raises instead of inventing a number.
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": _ago(5),
                     "detail": "draining"},
                    {"event": "fire_refused", "routine": "alpha", "run_id": "", "ts": _ago(4),
                     "detail": "draining"},
                    {"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(72),
                     "detail": "no completed snapshot for 4 days"},
                    {"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(2),
                     "detail": "no completed snapshot for 4 days"},
                    {"event": "migration_due_soon", "routine": "", "run_id": "", "ts": _ago(3),
                     "detail": "1 MIGRATION marker expires 2026-11-20"}])
    out = blocked_fleet(server)
    assert "total" not in out, "one number over two units is the defect R3418 removes"
    assert out["incident_total"] == 2, "occurrences, over incident rows only"
    assert out["standing_rows"] == 2, "conditions standing — backup_stale and the marker"
    with pytest.raises(KeyError):
        sum(r["count"] for r in out["rows"])


def test_a_healthy_fleet_reads_zero_on_both_figures(tmp_path):
    """Zero is the good reading, and it has to be readable on both units."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [])
    out = blocked_fleet(server)
    assert out["incident_total"] == 0 and out["standing_rows"] == 0 and out["rows"] == []


def test_each_row_says_which_unit_its_magnitude_is_in(tmp_path):
    """A mixed window: the console branches on `kind` to pick a unit, so every row carries it.
    `obligation_overdue` is latched one row per routine per day (`daemon/owed_watch`), while a
    capped trigger is re-earned by real traffic each day.
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "trigger_capped", "routine": "beta", "run_id": "", "ts": _ago(2),
                     "detail": "daily cap of 5 reached"},
                    {"event": "obligation_overdue", "routine": "beta", "run_id": "",
                     "ts": _ago(48), "detail": "2 obligations open since 2026-11-01"},
                    {"event": "obligation_overdue", "routine": "beta", "run_id": "",
                     "ts": _ago(2), "detail": "2 obligations open since 2026-11-01"}])
    out = blocked_fleet(server)
    kinds = {row["event"]: row["kind"] for row in out["rows"]}
    assert kinds == {"trigger_capped": "incident", "obligation_overdue": "standing"}
    assert out["incident_total"] == 1 and out["standing_rows"] == 1
