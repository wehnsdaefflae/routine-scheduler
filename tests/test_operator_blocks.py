"""Blocks only the operator can clear (F796): the classification, the served count, the badge.

The blocked-work fold answers "what did the fleet owe and not deliver". These tests pin the
second question it never answered — who can clear it — and the surface that follows from it:
`GET /api/health/blocked` carries `operator_total` and marks each row, so a block waiting on a
person is counted where he cannot miss it instead of only inside one Stats panel's 7-day
window.
"""

import json
from datetime import UTC, datetime, timedelta

from conftest import authed_client, make_test_server
from helpers import tmp_server
from rsched.daemon import pause
from rsched.readmodels import memo, operator_blocks
from rsched.readmodels.health_stream import BLOCKED_EVENTS, blocked_fleet
from rsched.readmodels.operator_blocks import OPERATOR_CLEARABLE, annotate


def _ago(hours):
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _write(server, rows):
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


def test_every_operator_clearable_name_is_a_blocked_event():
    """The fold keeps only `BLOCKED_EVENTS`, so a name that drifts out of that set can never
    appear in a row — this module would then count zero of them forever, silently.
    """
    assert operator_blocks.unknown_names() == set()
    for name, sentence in OPERATOR_CLEARABLE.items():
        assert name in BLOCKED_EVENTS, name
        assert sentence.strip(), f"{name} needs a sentence naming what only he can do"


def test_the_loud_run_measurements_are_not_operator_blocks():
    """A badge that is never dark is the same as no badge. `wrong_turn_detected` fired 202
    times in three weeks on this instance, and nothing about it waits for a person: the engine
    re-drives the turn. Same for the other two measurements of runs that DID happen.
    """
    for name in ("wrong_turn_detected", "process_overhead_high", "finish_gate_failed"):
        assert not operator_blocks.is_operator_block(name), name


def test_what_clears_itself_is_not_his_to_clear():
    """Each of these names its own clearing in its own vocabulary line: the next fire, the
    next tick, the daily cap reset, the cooldown lapsing, a later commit, a routine's own run.
    """
    for name in ("fire_refused", "scheduler_tick_error", "trigger_capped",
                 "model_chain_exhausted", "commit_failed", "obligation_overdue",
                 "item_dropped_live", "migration_expired"):
        assert not operator_blocks.is_operator_block(name), name


def test_a_stale_backup_is_a_block_only_he_can_clear():
    """`rsched-backup.service` is a host unit. No run can start it, and the instance keeps
    running unbacked meanwhile — nineteen unnoticed nights before the Stats panel existed.
    """
    assert operator_blocks.is_operator_block("backup_stale")
    assert "unbacked" in OPERATOR_CLEARABLE["backup_stale"]


def test_the_count_is_distinct_rows_not_occurrences(tmp_path):
    """A pause that ate eight of ONE routine's fires is one thing to go and fix. The badge
    carries rows, so eight occurrences of one (event, subject) count once and two subjects
    count twice — while `incident_total` keeps counting occurrences for the panel. Both
    subjects here are `fire_paused`, an INCIDENT: each dropped fire is a fire that did not
    happen, so counting them is counting facts (R3418 · `readmodels/blocked_kinds`).
    """
    server = tmp_server(tmp_path, create=False)
    rows = [{"event": "fire_paused", "routine": "scheduler-builder", "run_id": "",
             "ts": _ago(h), "detail": "due fire skipped because scheduling is paused"}
            for h in range(1, 9)]
    rows.append({"event": "fire_paused", "routine": "self-audit", "run_id": "",
                 "ts": _ago(2), "detail": "due fire skipped because scheduling is paused"})
    _write(server, rows)

    payload = annotate(blocked_fleet(server))

    assert payload["incident_total"] == 9, "the panel still counts incident occurrences"
    assert payload["operator_total"] == 2, "the badge counts rows — one per thing to fix"


def test_a_row_carries_what_only_he_can_do_about_it(tmp_path):
    """The sentence is SERVED, like `vocabulary`: the console renders a judgement it was not
    compiled with, so the classification lives in one place.
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [{"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(3),
                     "detail": "the last COMPLETE state snapshot finished 2026-11-02"}])

    payload = annotate(blocked_fleet(server))

    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["operator"] is True
    assert "journalctl" in row["waiting_on_you"]
    assert payload["operator_vocabulary"]["backup_stale"] == row["waiting_on_you"]


def test_a_block_a_run_will_clear_is_marked_not_his(tmp_path):
    """Both kinds reach the same fold, so the marking must distinguish them row by row rather
    than filtering: the Stats panel still shows everything.
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        {"event": "backup_stale", "routine": "", "run_id": "", "ts": _ago(1), "detail": "x"},
        {"event": "wrong_turn_detected", "routine": "freelance-hunter", "run_id": "r:1",
         "ts": _ago(1), "detail": "a turn came back wrong"}])

    payload = annotate(blocked_fleet(server))

    marks = {row["event"]: row["operator"] for row in payload["rows"]}
    assert marks == {"backup_stale": True, "wrong_turn_detected": False}
    assert payload["operator_total"] == 1
    assert "waiting_on_you" not in next(r for r in payload["rows"]
                                        if r["event"] == "wrong_turn_detected")


def test_zero_is_the_good_reading_and_is_served(tmp_path):
    """A healthy instance must serve the count as 0 rather than omit it: a badge reading an
    absent key cannot tell "nothing is blocked" from "this daemon does not answer that".
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [])

    payload = annotate(blocked_fleet(server))

    assert payload["operator_total"] == 0
    assert payload["rows"] == []


def test_the_route_serves_the_operators_share(tmp_path):
    """The endpoint, not just the fold: the badge reads `/api/health/blocked`, so the count has
    to be on the wire.
    """
    server = make_test_server(tmp_path)
    _write(server, [{"event": "lane_fire_paused", "routine": "grp-7a1c", "run_id": "",
                     "ts": _ago(5), "detail": "nightly: due lane fire skipped"}])
    pause.set_paused(server, True)          # the pause still holds: it is his to lift

    with authed_client(server) as client:
        r = client.get("/api/health/blocked")

    assert r.status_code == 200
    body = r.json()
    assert body["operator_total"] == 1
    assert body["rows"][0]["operator"] is True
    assert "resume scheduling" in body["rows"][0]["waiting_on_you"]


def test_a_block_whose_condition_is_over_leaves_the_badge_and_stays_as_history(tmp_path):
    """2026-11-09: the badge read 10 — nine fires skipped under a pause lifted five days earlier
    and a backup that had come back. A row whose condition is KNOWN to be over is `resolved`."""
    server = make_test_server(tmp_path)
    _write(server, [{"event": "fire_paused", "routine": "scheduler-builder", "run_id": "",
                     "ts": _ago(5), "detail": "due fire skipped because scheduling is paused"}])
    with authed_client(server) as client:          # no pause file: scheduling runs
        body = client.get("/api/health/blocked").json()
    [row] = body["rows"]
    assert body["operator_total"] == 0
    assert row["operator"] is False and row["resolved"] is True
    assert "no longer paused" in row["resolved_note"]


def test_a_condition_that_cannot_be_told_keeps_counting():
    """Only a known `False` retires a row: an unknown backup verdict is not a fresh backup."""
    payload = {"rows": [{"event": "backup_stale", "subject": "", "count": 1}]}
    unknown = operator_blocks.annotate(payload, still_holds=lambda _e, _s: None)
    assert unknown["operator_total"] == 1 and not unknown["rows"][0].get("resolved")
    over = operator_blocks.annotate(payload, still_holds=lambda _e, _s: False)
    assert over["operator_total"] == 0 and over["rows"][0]["resolved"] is True


def test_an_undelivered_answer_is_his_to_clear():
    assert operator_blocks.is_operator_block("answer_undelivered")
    assert "Decisions page" in OPERATOR_CLEARABLE["answer_undelivered"]
