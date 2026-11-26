"""Completed task and edit windows retain what the engine actually measured."""
from conftest import finish, write_file
from helpers import server_for
from rsched.engine.history import prior_legs
from rsched.engine.inbox import file_message
from rsched.engine.revise import NARROW, write_revise_marker
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import read_json

TS = "20261126-000000"


def test_task_budget_stop_survives_an_edit_leg(make_routine, scripted):
    d = make_routine(slug="window", budgets={"max_turns": 1, "max_wall_clock_min": 7})
    server = server_for(d)
    scripted([write_file("state/probe.txt", content="probe"), finish(status="partial")])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "partial"
    task = read_json(run_dir / "status.json")["budgets"]["finished_window"]
    assert task["kind"] == "task"
    assert task["max_turns"] == 1
    assert task["wall_limit_s"] == 420
    assert task["elapsed_s"] >= 0
    assert task["budget_stop"] == {"resource": "turns", "limit": 1}

    text = "The operator approved a narrow recipe edit."
    write_revise_marker(run_dir, text, leg=NARROW)
    file_message(d, text, via="web-converse")
    scripted([finish(summary="No recipe change needed.")])
    status, _ = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "partial"  # The edit must not rewrite the task verdict.
    block = read_json(run_dir / "status.json")["budgets"]
    edit = block["finished_window"]
    assert block["legs"][-1]["finished_window"] == task
    assert edit["kind"] == "edit" and edit["budget_stop"] is None
    assert edit["wall_limit_s"] == 420 and edit["elapsed_s"] >= 0
    assert edit["base_turn"] == task["base_turn"] + task["window_turns"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert [e["payload"]["budget_window"] for e in events if e["type"] == "finish"] == [task, edit]


def test_old_and_interrupted_status_never_become_completed_window_evidence():
    old = {"budgets": {"base_turn": 0, "window_turns": 4, "max_turns": 10}}
    interrupted = {"state": "aborted", "budgets": {**old["budgets"], "wall_clock_left_s": 35}}
    for status in (old, interrupted):
        assert "finished_window" not in prior_legs(status)[-1]
