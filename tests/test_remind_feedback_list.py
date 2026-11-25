"""Several reminders fire on ONE turn, so one turn can label several (F925). There is no cap on
concurrent fires and the engine asks for every fire to be labelled — "an unlabelled fire is a
turn spent for no evidence" — while the field accepted a label for exactly one, so a run had to
drop evidence or spend a turn being refused. Measured as 9 wrong turns in 14 days across five
routines, each rejected with `... is not of type 'object'`; five routines reached for the list
form independently, which is the shape the contract implies.
"""

from __future__ import annotations

from conftest import finish, util
from helpers import prompt_text, reminder, seed_local_reminders, server_for, set_capabilities
from rsched import reminders as store
from rsched.engine.actions import normalize_action, validate_action
from rsched.engine.fieldshapes import canonical
from rsched.engine.runtime import run_routine
from rsched.policyload import load_policy

TS = "20260712-070000"


def _problems(tmp_path, feedback):
    on = load_policy(tmp_path, [], {"reminders": "local"})
    return validate_action({"say": "s", "kind": "read_file", "path": "a.md",
                            "remind_feedback": feedback}, grants=on)


def test_a_list_of_labels_is_accepted(tmp_path):
    """The whole point: one action, two fires, two labels — judged at BOTH layers. The JSON
    Schema is the gate that was refusing this (`... is not of type 'object'`), and
    `validate_action` runs only on an object that already passed it, so asserting the semantic
    layer alone would pass while the schema still rejected every such turn."""
    from rsched.engine.actionschema import ACTION_SCHEMA
    from rsched.schema_guard import validate

    action = {"say": "s", "kind": "read_file", "path": "a.md",
              "remind_feedback": [{"id": "rem-1", "label": "would_have"},
                                  {"id": "rem-2", "label": "could_not"}]}
    assert not validate(action, ACTION_SCHEMA)     # the layer that was refusing the shape
    assert not _problems(tmp_path, action["remind_feedback"])


def test_one_object_is_still_accepted(tmp_path):
    """The single form is what every existing caller writes; a list must not replace it."""
    assert not _problems(tmp_path, {"id": "rem-1", "label": "would_have"})


def test_an_entry_in_a_list_is_judged_like_a_lone_one(tmp_path):
    """Each entry carries for itself. A list must not become a hole where a bad label passes."""
    bad = _problems(tmp_path, [{"id": "rem-1", "label": "would_have"},
                               {"id": "rem-2", "label": "nope"}])
    assert bad and "must be one of" in bad[0]
    missing = _problems(tmp_path, [{"label": "did"}])
    assert missing and "must name the reminder" in missing[0]


def test_the_same_reminder_twice_is_refused(tmp_path):
    """A label answers one hold ONCE: a second entry for the same id is evidence about nothing,
    and silently counting it would inflate the tally the pattern is tuned against."""
    dup = _problems(tmp_path, [{"id": "rem-1", "label": "did"},
                               {"id": "rem-1", "label": "didnt"}])
    assert dup and "same reminder twice" in dup[0]


def test_the_refusal_of_a_non_object_teaches_the_list_form(tmp_path):
    """The field's error has to name both shapes, or a run that wrote neither learns only half
    the contract and the next attempt costs another turn."""
    bad = _problems(tmp_path, "rem-1 would_have")
    assert bad and "must be an object" in bad[0] and "LIST" in bad[0]


def test_shorthand_is_repaired_per_entry():
    """`{<id>: <label>}` is repaired for a lone entry, so it must be repaired INSIDE a list too
    — a run writing one entry in shorthand beside one in full would otherwise lose both."""
    out = canonical({"kind": "read_file", "remind_feedback": [
        {"rem-1": "would_have"}, {"id": "rem-2", "label": "did"}]})
    assert out["remind_feedback"] == [{"id": "rem-1", "label": "would_have"},
                                      {"id": "rem-2", "label": "did"}]


def test_a_one_element_list_still_collapses_to_the_object():
    """Both spell the same thing, and the storage sees one convention."""
    out = canonical({"kind": "read_file",
                     "remind_feedback": [{"id": "rem-1", "label": "did"}]})
    assert out["remind_feedback"] == {"id": "rem-1", "label": "did"}


def test_two_fires_on_one_action_both_land_on_their_tallies(make_routine, scripted):
    """End to end, and the case the refusals came from: TWO reminders match one action, so the
    next action carries two labels and each reminder's own tally records its own outcome.
    Before this, the run could record one and had to drop the other."""
    d = make_routine(slug="fbl")
    set_capabilities(d, reminders="local")
    seed_local_reminders(d, [
        reminder(rid="rem-a", regex="^util:danger", desc="it wipes the workdir"),
        reminder(rid="rem-b", regex="^util:danger .*--wipe", desc="--wipe takes no confirmation"),
    ], {})
    ep = scripted([
        util("danger", args=["--wipe"]),
        {"say": "Both labelled.", "kind": "read_file", "path": "state/notes.md",
         "remind_feedback": [{"id": "rem-a", "label": "would_have"},
                             {"id": "rem-b", "label": "could_not"}]},
        finish(),
    ])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    tallies = {r.id: r.stats for r in store.load_local(d)[0]}
    assert tallies["rem-a"]["would_have"] == 1, tallies
    assert tallies["rem-b"]["could_not"] == 1, tallies
    shown = prompt_text(ep)
    assert "rem-a labelled would_have" in shown and "rem-b labelled could_not" in shown


def test_the_schema_itself_offers_both_shapes():
    """The ACTION SCHEMA is what a run reads before writing the field — a list the validator
    accepts but the schema does not describe is a contract only the refusals teach."""
    from rsched.engine.actionschema import ACTION_SCHEMA

    field = ACTION_SCHEMA["properties"]["remind_feedback"]
    assert set(field["type"]) == {"object", "array"}, field["type"]
    assert "LIST" in field["description"]
    # ONE schema over both types, never a `oneOf`: that renders the entry's property prose
    # TWICE into the always-on dump, measured at +1,568 chars against a ceiling the same
    # release's own work had just lowered.
    assert "oneOf" not in field


def test_normalize_keeps_a_list_through_the_action_seam():
    """`normalize_action` is the one call site the engine repairs through; a list must survive
    it unchanged rather than being flattened to its first entry."""
    out = normalize_action({"say": "s", "kind": "read_file", "path": "a.md",
                            "remind_feedback": [{"id": "rem-1", "label": "did"},
                                                {"id": "rem-2", "label": "didnt"}]})
    assert out["remind_feedback"] == [{"id": "rem-1", "label": "did"},
                                      {"id": "rem-2", "label": "didnt"}]
