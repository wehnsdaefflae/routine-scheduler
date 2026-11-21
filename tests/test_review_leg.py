"""The post-run REVIEW LEG (engine/reviewleg.py): a routine run whose own record shows it had
something to learn from looks back once, on the same message list, before its close-out.

What these hold: it runs only on a reason and never on a clean run; its notes land and are
committed with the run; its two note guards (read before revise, delete only to fold); its span
is left out of the task's overhead measurement; a RECIPE REVISION it names joins the run's one
D200 card; and every way it can go wrong costs the run nothing but the leg.
"""

from __future__ import annotations

import pytest

from conftest import finish
from helpers import server_for
from rsched import pending
from rsched.engine import overhead, reviewleg
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261121-070000"


@pytest.fixture(autouse=True)
def _leg_on(monkeypatch):
    monkeypatch.delenv("RSCHED_SKIP_REVIEW_LEG", raising=False)   # conftest sets it suite-wide


def _read(path: str) -> dict:
    return {"say": f"Reading {path}.", "kind": "read_file", "path": path}


def _note(name: str, content: str = "A general rule — because it held.", **extra) -> dict:
    return {"say": "Keeping what this run showed.", "kind": "memory_write", "name": name,
            "content": content, "about": f"what {name} holds", **extra}


#: Two failing reads (missing files) and four clean ones: six task turns, two failures.
TASK_WITH_FAILURES = [_read("missing-a.txt"), _read("missing-b.txt"), _read("main.md"),
                      _read("LEDGER.md"), _read("routine.yaml"), _read("state")]
#: The same length with nothing gone wrong.
CLEAN_TASK = [_read("main.md"), _read("LEDGER.md"), _read("routine.yaml"), _read("state"),
              _read("inbox"), _read(".")]


def _run(make_routine, scripted, replies, **server):
    d = make_routine(slug="reviewr", budgets={"max_turns": 40})
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d, **server), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, run_dir, events


def test_a_run_with_failures_gets_a_leg_and_its_note_lands(make_routine, scripted):
    d, _ep, status, run_dir, events = _run(make_routine, scripted, [
        *TASK_WITH_FAILURES, finish(summary="task done"),
        _note("missing-files", "A path the recipe names may not exist — list the dir first, "
                               "because two reads failed on guessed names."),
        finish(summary="kept one rule"),
    ])
    assert status == "ok"
    assert (d / ".memory" / "missing-files.md").is_file()
    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert "task done" in result
    assert "[Review leg (2 of its actions failed): 2 turns; notes written: missing-files.]" in result
    # the leg's note opened it and every event it wrote is marked; the finish event is last
    opening = next(i for i, e in enumerate(events) if e["type"] == "user_injection"
                   and e["payload"].get("review_leg"))
    leg = list(events[opening + 1:-1])
    assert leg and all(e.get("review_leg") for e in leg)
    assert events[-1]["type"] == "finish"
    note = events[opening]["payload"]["text"]
    assert note.startswith("REVIEW LEG. This run has FINISHED (status ok)")
    assert "because 2 of its actions failed" in note
    # the task's overhead counts the six task actions and the task's finish — not the leg
    measured = overhead.of_run(run_dir, d)
    assert measured is not None
    assert sum(measured[k] for k in ("task", "process", "friction")) == 7


def test_a_clean_run_gets_no_leg(make_routine, scripted):
    _d, ep, status, run_dir, events = _run(make_routine, scripted,
                                           [*CLEAN_TASK, finish(summary="clean")])
    assert status == "ok"
    assert not any(e["payload"].get("review_leg") for e in events
                   if isinstance(e.get("payload"), dict))
    assert "Review leg" not in (run_dir / "result.md").read_text(encoding="utf-8")
    assert not ep.replies       # no completion beyond the scripted task


def test_the_instance_switch_turns_it_off(make_routine, scripted):
    _d, ep, _status, run_dir, _events = _run(
        make_routine, scripted, [*TASK_WITH_FAILURES, finish(summary="done")], review_leg=False)
    assert "Review leg" not in (run_dir / "result.md").read_text(encoding="utf-8")
    assert not ep.replies


def test_a_run_that_kept_its_own_notes_gets_no_leg(make_routine, scripted):
    """Hermes' back-off: a foreground memory write resets the counter."""
    _d, ep, _status, run_dir, _events = _run(make_routine, scripted, [
        *TASK_WITH_FAILURES, _note("own-note"), finish(summary="done")])
    assert "Review leg" not in (run_dir / "result.md").read_text(encoding="utf-8")
    assert not ep.replies


def test_a_note_is_revised_only_after_the_leg_read_it(make_routine, scripted):
    d = make_routine(slug="reviewr", budgets={"max_turns": 40})
    (d / ".memory").mkdir()
    (d / ".memory" / "paths.md").write_text("old\n", encoding="utf-8")
    scripted([*TASK_WITH_FAILURES, finish(summary="done"),
              _note("paths", "new rule"),
              {"say": "Reading it first.", "kind": "memory_read", "name": "paths"},
              _note("paths", "new rule — because it held"),
              finish(summary="revised")])
    run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(d / "runs" / TS / "transcript.jsonl")
    refused = [e for e in events if e["type"] == "observation" and e.get("review_leg")
               and e["payload"].get("error")]
    assert len(refused) == 1
    assert "memory_read it in this leg before you revise it" in refused[0]["payload"]["error"]
    assert "because it held" in (d / ".memory" / "paths.md").read_text(encoding="utf-8")


def test_a_note_is_deleted_only_to_fold_it_into_another(make_routine, scripted):
    d = make_routine(slug="reviewr", budgets={"max_turns": 40})
    (d / ".memory").mkdir()
    (d / ".memory" / "old.md").write_text("old\n", encoding="utf-8")
    delete = {"say": "Dropping it.", "kind": "memory_write", "name": "old", "delete": True}
    scripted([*TASK_WITH_FAILURES, finish(summary="done"),
              delete,                                                  # refused: not folded
              {"say": "Reading it.", "kind": "memory_read", "name": "old"},
              _note("merged", "what old held, kept"), delete,          # folded: lands
              finish(summary="folded")])
    run_routine(d, server_for(d), run_ts=TS)
    assert not (d / ".memory" / "old.md").exists()
    assert (d / ".memory" / "merged.md").is_file()
    result = (d / "runs" / TS / "result.md").read_text(encoding="utf-8")
    assert "folded and deleted: old" in result


def test_a_recipe_revision_from_the_leg_joins_the_runs_card(make_routine, scripted):
    d, _ep, _status, _run_dir, events = _run(make_routine, scripted, [
        *TASK_WITH_FAILURES, finish(summary="done"),
        finish(summary="nothing to keep\nRECIPE REVISION: main.md — list a directory before "
                       "reading a file it may not hold, because guessed names fail")])
    final = events[-1]["payload"]["summary"]
    assert "RECIPE REVISION: main.md — list a directory" in final
    cards = [r for r in pending.load_all(d.parent) if r.get("routine") == "reviewr"]
    assert len(cards) == 1
    assert any("list a directory" in line for line in cards[0]["fields"]["lines"])


def test_a_leg_that_breaks_costs_the_run_nothing(make_routine, scripted):
    """The scripted endpoint runs out of replies inside the leg: the run still closes out."""
    _d, _ep, status, run_dir, events = _run(make_routine, scripted,
                                            [*TASK_WITH_FAILURES, finish(summary="task done")])
    assert status == "ok"
    assert events[-1]["type"] == "finish"
    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert "task done" in result
    assert "nothing kept; stopped: AssertionError" in result


def test_the_leg_has_a_turn_backstop(make_routine, scripted):
    reads = [_read("main.md"), _read("LEDGER.md")] * reviewleg.REVIEW_TURNS
    _d, _ep, status, run_dir, _events = _run(make_routine, scripted, [
        *TASK_WITH_FAILURES, finish(summary="done"), *reads[:reviewleg.REVIEW_TURNS]])
    assert status == "ok"
    assert (f"{reviewleg.REVIEW_TURNS} turns; nothing kept; stopped: backstop"
            in (run_dir / "result.md").read_text(encoding="utf-8"))
