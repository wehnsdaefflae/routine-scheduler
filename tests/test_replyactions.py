"""A reply that carries more than one action runs only the FIRST, and the run is told by name
which did not run (engine/replyactions.py — the dropped-first-action of R2443-R2445)."""

import json

import pytest

from conftest import finish, write_file
from helpers import server_for
from rsched.endpoints.base import Completion
from rsched.engine.observations import format_observation
from rsched.engine.replyactions import (
    UNREADABLE_BESIDE_CALL,
    reply_actions,
    unexecuted_note,
    unexecuted_row,
    written_actions,
)
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.schema_guard import SchemaViolation

EDIT = {"say": "Adding the two fields to ProbeOutcome.", "kind": "edit_file",
        "path": "models.py", "anchor": "a", "replacement": "b"}
READ = {"say": "Fields added. Now reading the runner.", "kind": "read_file",
        "path": "runner.py"}


def test_the_text_action_comes_first_and_the_call_after_it():
    """The live Opus shape: action as JSON text, then the call narrating it as done."""
    assert reply_actions(Completion(text=json.dumps(EDIT), parsed=READ)) == [EDIT, READ]


def test_two_text_actions_in_one_reply_are_both_read_in_order():
    """With no tool at all a thinking model still writes two, as two text blocks."""
    text = json.dumps(EDIT) + "\n" + json.dumps(READ)
    assert reply_actions(Completion(text=text)) == [EDIT, READ]


def test_an_action_written_twice_is_one_action():
    """Narration aside, the same object twice — the scripted harness's text+parsed, or a model
    writing its call out as text too — runs once with nothing to report: a duplicate named as
    "not run" would invite the run to repeat an edit."""
    assert reply_actions(Completion(text=json.dumps(EDIT), parsed=EDIT)) == [EDIT]
    retold = {**EDIT, "say": "Same edit, said differently.", "note": "n"}
    assert reply_actions(Completion(text=json.dumps(retold), parsed=EDIT)) == [retold]


def test_an_action_quoted_inside_another_is_not_a_second_one():
    outer = {"say": "Saving the fixture.", "kind": "write_file", "path": "f.json",
             "content": json.dumps(READ)}
    assert written_actions(json.dumps(outer)) == [outer]


def test_prose_beside_a_call_is_not_an_action_and_a_lone_reply_still_parses():
    assert reply_actions(Completion(text="Reading the runner next.", parsed=READ)) == [READ]
    assert reply_actions(Completion(text='{"note": "no kind here"}')) == [{"note": "no kind here"}]
    with pytest.raises(SchemaViolation):
        reply_actions(Completion(text="no json at all"))


def test_an_unreadable_text_action_beside_a_call_runs_neither():
    """The one shape in which the engine cannot know what came first: it runs nothing and asks
    for the one action, rather than run the call that may presuppose the unreadable one."""
    broken = '{"say": "Adding the import", "kind": "edit_file", "path": "r.py", "anchor": '
    with pytest.raises(SchemaViolation) as exc:
        reply_actions(Completion(text=broken, parsed=READ))
    assert exc.value.problems == [UNREADABLE_BESIDE_CALL]


def test_the_note_names_each_unrun_action_and_its_claim():
    assert unexecuted_note([]) == "" and unexecuted_note(None) == ""
    one = unexecuted_note([unexecuted_row(READ)])
    assert "carried 2 actions" in one and "This one did NOT run" in one
    assert "read_file path=runner.py" in one and '"Fields added. Now reading the runner."' in one
    two = unexecuted_note([unexecuted_row(READ), unexecuted_row(EDIT)])
    assert "carried 3 actions" in two and "These 2 did NOT run, and nothing they" in two
    obs = {"kind": "write_file", "path": "x", "bytes": 1, "not_executed": [unexecuted_row(READ)]}
    assert format_observation(obs).endswith(one)


def test_a_two_action_reply_runs_the_first_and_tells_the_run_what_did_not_run(
        make_routine, scripted):
    """End to end: the first action's file is written, the second's is not, the observation the
    run reads next names the second with its own claim, and the transcript keeps the record."""
    d = make_routine(slug="dual")
    first = write_file("state/first.txt", content="1", say="Writing the first file.")
    second = write_file("state/second.txt", content="2",
                        say="First file written. Now the second.")
    ep = scripted([Completion(text=json.dumps(first), parsed=second,
                              usage={"in": 1, "out": 1}),
                   write_file("state/after.txt", say="Writing after the note."),
                   finish(summary="done")])
    status, run_dir = run_routine(d, server_for(d), run_ts="20261010-090000")
    assert status == "ok"
    assert (d / "state" / "first.txt").exists()
    assert not (d / "state" / "second.txt").exists()
    seen = ep.calls[1]["messages"]
    assert json.loads(seen[-2]["content"]) == first          # the turn IS the first action
    assert "NOT EXECUTED" in seen[-1]["content"]
    assert "First file written. Now the second." in seen[-1]["content"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = next(e["payload"] for e in events if e["type"] == "observation")
    assert obs["not_executed"] == [unexecuted_row(second)]
    later = [e["payload"] for e in events if e["type"] == "observation"][1:]
    assert all("not_executed" not in p for p in later)       # one turn's list, never carried
