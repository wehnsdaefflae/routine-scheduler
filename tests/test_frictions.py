"""A run's FRICTIONS (engine/frictions.py) — what went wrong, by a signature every run of every
routine names the same way, counted from the events the engine already writes."""

from __future__ import annotations

import gzip
import json

from rsched.engine import frictions


def _obs(**payload):
    return {"type": "observation", "payload": payload}


def test_each_failure_the_engine_already_records_gets_its_signature():
    events = [
        {"type": "error", "payload": {"where": "schema", "message": "x"}},
        {"type": "error", "payload": {"where": "schema", "message": "y"}},
        _obs(kind="script", name="sync", exit=1),
        _obs(kind="util", name="gmail", exit=2),
        _obs(kind="edit_file", error="anchor not found in scripts/x.py"),
        _obs(kind="read_file", error="[Errno 2] No such file or directory: 'a'"),
        _obs(kind="memory_write", error="notes are capped at 100"),
        _obs(kind="delete", error="this deletes a path outside the routine's own dir"),
        _obs(kind="finish", rejected=True, accounting=["d2"], message="m"),
        _obs(kind="finish", rejected=True, message="no action at all"),
        {"type": "user_injection", "payload": {"text": "t", "source": "engine",
                                               "budget": "turns"}},
    ]
    assert frictions.from_events(events) == {
        "budget:turns": 1, "deferral:accounting": 1, "deferral:no_action": 1,
        "edit_miss": 1, "memory_cap": 1, "read_miss": 1, "refused:delete": 1,
        "schema": 2, "script:sync": 1, "util:gmail": 1}


def test_what_names_no_failure_of_the_harness_is_not_a_signature():
    events = [
        _obs(kind="shell", command="grep x", exit=1),            # grep's "no match"
        _obs(kind="script", name="ok", exit=0),
        _obs(kind="reminder_hold", error="held"),
        {"type": "error", "payload": {"where": "endpoint", "message": "HTTP 429"}},
        {"type": "refusal", "payload": {"where": "loop", "message": "declined"}},
        _obs(kind="edit_file", error="the edit would leave scripts/x.json unparseable"),
    ]
    assert frictions.from_events(events) == {"refused:edit_file": 1}


def test_only_the_final_accounting_counts_and_a_goal_never_recurs():
    events = [
        {"type": "stopping_update", "payload": {"judged": {"d1": "unmet", "d2": "met"}}},
        {"type": "stopping_update", "payload": {"judged": {"d1": "met", "d3": "unmet",
                                                           "b1": "unmet"}}},
    ]
    assert frictions.from_events(events) == {"unmet:d3": 1}


def test_no_transcript_is_unknown_and_a_rotated_one_reads_the_same(tmp_path):
    assert frictions.of_run(tmp_path) is None
    line = json.dumps({"type": "error", "payload": {"where": "schema"}}).encode()
    (tmp_path / "transcript.jsonl.gz").write_bytes(gzip.compress(line + b"\n{torn"))
    assert frictions.of_run(tmp_path) == {"schema": 1}
    (tmp_path / "transcript.jsonl").write_bytes(b"")
    assert frictions.of_run(tmp_path) == {}               # read, and nothing went wrong
