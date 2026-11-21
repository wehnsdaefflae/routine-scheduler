"""A tool parameter that swallowed the ones after it — repaired before the validator sees it."""

from __future__ import annotations

from pathlib import Path

from conftest import finish, make_test_server  # noqa: F401  (fixture import side effects)
from helpers import health_events, server_for
from rsched.engine import paramsalvage
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261022-100000"

#: The exact shape the birthday-admin run produced, shortened: a summary that closed itself
#: with its own tag name and swallowed the parameter written after it.
SWALLOWED_SUMMARY = (
    "Outcome: bookings_confirmed 1, vendor_replies 14. Another quiet data week.</summary>\n"
    '<parameter name="accounting">["d1 met: hwm advanced 363 to 364", "d2 met: 62 messages '
    'swept, 0 relevant"]'
)


def test_the_swallowed_parameter_is_restored_to_its_own_field():
    action = {"say": "Finishing.", "kind": "finish", "status": "ok",
              "summary": SWALLOWED_SUMMARY}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == ["accounting"]
    assert fixed["summary"] == ("Outcome: bookings_confirmed 1, vendor_replies 14. "
                                "Another quiet data week.")
    assert fixed["accounting"] == ["d1 met: hwm advanced 363 to 364",
                                   "d2 met: 62 messages swept, 0 relevant"]


def test_a_swallowed_string_parameter_is_restored_as_a_string():
    """Not every swallowed field is JSON: `status`, `path`, `name` are plain scalars."""
    action = {"say": "Writing.", "kind": "write_file",
              "path": 'notes.md</path>\n<parameter name="content">hello world',
              "content": ""}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == ["content"]
    assert fixed["path"] == "notes.md"
    assert fixed["content"] == "hello world"


def test_several_swallowed_parameters_are_all_restored():
    action = {"say": "Reporting.", "kind": "report",
              "title": 'A defect</title>\n<parameter name="detail">The long detail.'
                       '</detail>\n<parameter name="target">scheduler-builder'}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == ["detail", "target"]
    assert fixed["title"] == "A defect"
    assert fixed["detail"] == "The long detail."
    assert fixed["target"] == "scheduler-builder"


def test_an_unaffected_action_is_returned_untouched():
    """The repair must be invisible to every well-formed turn — the overwhelming majority."""
    action = {"say": "Reading.", "kind": "read_file", "path": "main.md"}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == []
    assert fixed == action
    assert fixed is action                      # not even a copy: no allocation on the hot path


def test_prose_that_merely_mentions_a_parameter_tag_is_not_repaired():
    """A run auditing THIS defect quotes the broken shape in its own summary. Repairing that
    would eat the text it was reporting, so the marker must name a field of the SAME action
    AND the closer must match the field it is closing."""
    action = {"say": "Reporting the defect.", "kind": "finish", "status": "ok",
              "summary": 'The model wrote `</summary>` then `<parameter name="accounting">` '
                         "and the field vanished."}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == []
    assert fixed is action


def test_a_field_the_action_already_carries_is_never_overwritten():
    """If the provider delivered `accounting` too, the model's own value wins — the salvage
    only ever fills a hole."""
    action = {"say": "Finishing.", "kind": "finish", "status": "ok",
              "summary": SWALLOWED_SUMMARY, "accounting": ["d1 met: the real one"]}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == []
    assert fixed is action


def test_an_unknown_parameter_name_is_left_alone():
    """Only a real action field is restorable; anything else is the model's own prose."""
    action = {"say": "Finishing.", "kind": "finish", "status": "ok",
              "summary": 'done</summary>\n<parameter name="not_a_field">xyz'}
    fixed, salvaged = paramsalvage.repair(action)
    assert salvaged == []
    assert fixed is action


# --- end to end, through a real run -----------------------------------------------------


def test_a_run_whose_finish_swallowed_its_accounting_still_lands_it(make_routine, scripted):
    """The whole point: the turn the birthday-admin run could not get through now lands on
    its first attempt, with the accounting the model actually wrote."""
    d = make_routine(slug="salvr")
    scripted([
        # a finish is the SECOND action: the engine refuses a top-level finish(ok) before
        # anything ran, which has nothing to do with the salvage
        {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"},
        {"say": "Finishing.", "kind": "finish", "status": "ok",
         "summary": "All done here.</summary>\n"
                    '<parameter name="accounting">["d1 met: the work is on disk"]'},
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    landed = [e["payload"] for e in events if e["type"] == "assistant_action"]
    assert status == "ok"
    assert [a["kind"] for a in landed] == ["read_file", "finish"]
    assert landed[-1]["summary"] == "All done here."
    assert landed[-1]["accounting"] == ["d1 met: the work is on disk"]


def test_each_salvage_is_filed_so_the_rate_is_readable(make_routine, scripted):
    """A silent repair hides the provider defect it is papering over. One row per run."""
    d = make_routine(slug="salvf")
    scripted([
        {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"},
        {"say": "Finishing.", "kind": "finish", "status": "ok",
         "summary": 'Done.</summary>\n<parameter name="accounting">["d1 met: yes"]'},
    ])
    run_routine(d, server_for(d), run_ts=TS)
    rows = health_events(Path(d.parent), event=paramsalvage.EVENT)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["routine"] == "salvf"
    assert row["fields"] == "accounting"
    assert row["carrier"] == "summary"
    assert "accounting" in row["detail"]


def test_the_event_is_not_counted_as_blocked_work():
    """The turn HAPPENED and its action ran — the opposite of work that was due and did not.
    `git_lock_cleared` is the precedent this follows."""
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert paramsalvage.EVENT not in BLOCKED_EVENTS


def test_the_event_is_in_the_documented_vocabulary():
    from rsched import health_vocab

    assert paramsalvage.EVENT in health_vocab.EVENTS
