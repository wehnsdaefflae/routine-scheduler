"""The RESERVED FINISH TURN — its cause on the record, and its guard edges.

A fifth of the fleet's runs end here (90 `budget_exhausted` in 21 days) and nothing said WHICH
budget tripped: `loopnudge` appended the violation to the message list alone and the health
event carried the model's summary. Reconstructing the cause from status.json worked for 22 of
the 90 — turns and wall clock only, because those are the two that block carried.
"""

import json
from types import SimpleNamespace

from conftest import finish, write_file
from rsched.config import ServerConfig
from rsched.engine.runtime import run_routine

TS = "20260708-070000"


def _server(routine_dir, tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.libraries_home = tmp_path / "test-library"
    return s


def _health(tmp_path) -> list[dict]:
    path = tmp_path / "routines" / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_budget_exhausted_names_the_budget_that_spent_the_run(make_routine, scripted, tmp_path):
    d = make_routine(slug="whichbudget", budgets={"max_turns": 3})
    scripted([*[write_file(f"state/p{i}.txt", say=f"Step {i}.") for i in range(3)],
              finish("partial", "three files in, resume at p3")])
    status, run_dir = run_routine(d, _server(d, tmp_path), run_ts=TS)

    assert status == "partial"
    rows = [e for e in _health(tmp_path) if e["event"] == "budget_exhausted"]
    assert len(rows) == 1
    assert rows[0]["resource"] == "turns" and rows[0]["limit"] == 3
    assert rows[0]["status"] == "partial"
    # …and status.json says the same thing in the same fields
    spent = json.loads((run_dir / "status.json").read_text())["budgets"]["spent"]
    assert spent["resource"] == "turns" and "turn budget exhausted" in spent["message"]


def test_the_reserved_turn_is_on_the_record_even_when_the_run_finishes_ok(
        make_routine, scripted, tmp_path):
    """The reserved turn is what decides, not the status. A run that spends it and still says
    `ok` was ended by a budget just as much, and used to leave no health event at all — 11
    such runs since 09-01, so the fleet's budget-forced endings read as fewer than they are.
    """
    d = make_routine(slug="okbudget", budgets={"max_turns": 2})
    scripted([*[write_file(f"state/p{i}.txt", say=f"Step {i}.") for i in range(2)],
              finish("ok", "everything asked for is done")])
    status, _run_dir = run_routine(d, _server(d, tmp_path), run_ts=TS)

    assert status == "ok"
    rows = [e for e in _health(tmp_path) if e["event"] == "budget_exhausted"]
    assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["resource"] == "turns"


def test_the_violation_is_a_transcript_event_not_only_a_message(make_routine, scripted,
                                                                tmp_path):
    from rsched.engine.transcript import read_events

    d = make_routine(slug="budevent", budgets={"max_turns": 2})
    scripted([*[write_file(f"state/p{i}.txt", say=f"Step {i}.") for i in range(2)],
              finish("partial", "stopped")])
    _status, run_dir = run_routine(d, _server(d, tmp_path), run_ts=TS)

    events, _ = read_events(run_dir / "transcript.jsonl")
    notes = [e["payload"]["text"] for e in events
             if e["type"] == "user_injection" and e["payload"].get("source") == "engine"]
    assert any("OBSERVATION (budget spent)" in t and "turn budget exhausted" in t for t in notes)


def test_a_non_finish_on_the_reserved_turn_is_not_executed(make_routine, scripted, tmp_path):
    """"This is your LAST turn — the engine executes nothing else" was only a promise.

    The reserved turn narrows the GRAMMAR to `finish`, but a provider without constrained
    decoding can emit any kind and the executor ran it. It is now refused at the dispatch
    seam and the loop force-finishes as before — the same ending, minus the action, and
    still exactly ONE completion past the cap.
    """
    from rsched.engine.transcript import read_events

    d = make_routine(slug="reservedkind", budgets={"max_turns": 2})
    ep = scripted([*[write_file(f"state/p{i}.txt", say=f"Step {i}.") for i in range(2)],
                   write_file("state/after-the-wall.txt", say="One more anyway.")])
    status, run_dir = run_routine(d, _server(d, tmp_path), run_ts=TS)

    assert status == "partial"
    assert not (d / "state" / "after-the-wall.txt").exists()
    assert len(ep.calls) == 3          # 2 budgeted turns + the reserved one, and no more
    events, _ = read_events(run_dir / "transcript.jsonl")
    refused = [e["payload"] for e in events
               if e["type"] == "observation" and e["payload"].get("rejected")]
    assert refused and "executes nothing but `finish`" in refused[-1]["reason"]


def test_reserve_finish_clears_a_shed_schema(make_routine):
    """A repeat streak sheds the action grammar for the next turns (`_shed_schema_turns`).
    The reserved turn re-armed `_schema_off` but not that counter, so a reserved turn landing
    right after a repeat warning ran WITHOUT the finish-only grammar it exists to impose.
    """
    from rsched.engine import loopnudge

    loop = SimpleNamespace(messages=[], _schema_off=True, _shed_schema_turns=2,
                           action_schema=None, _finish_reserved=False, _budget_spent=None,
                           ctx=SimpleNamespace(transcript=SimpleNamespace(
                               event=lambda *a, **k: None)))
    loopnudge.reserve_finish(loop, {"resource": "turns", "limit": 3,
                                    "message": "turn budget exhausted (3)"})

    assert loop._finish_reserved is True
    assert loop._schema_off is False and loop._shed_schema_turns == 0
    assert loop._budget_spent["resource"] == "turns"
    assert "turn budget exhausted (3)" in loop.messages[-1]["content"]
