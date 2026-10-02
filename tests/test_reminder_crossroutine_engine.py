"""D152-C step 2, the ENGINE half: setting a hook on another routine, and the target's say.

The store half (`test_reminder_crossroutine.py`) proves the file shapes. This proves the two
runs: routine A emits `remind {op: add, target: B}` and the hook lands in B's store; B's next
run meets it as FOREIGN, carries a disposition on the fire, and that disposition outranks A.
"""

import pytest

from conftest import finish, util, write_file
from helpers import prompt_text, server_for, set_capabilities
from rsched import reminders as store
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from test_reminders import TS


def _rem(rid="rem-1", regex="^util:danger", desc="it deletes the target", kind="action",
         owner="", **stats):
    return store.Reminder(id=rid, regex=regex, description=desc, scope="local", kind=kind,
                          owner=owner, created_run="r:1",
                          stats={**store.blank_stats(), **stats})


def test_a_run_sets_a_hook_on_another_routine_and_it_lands_in_that_routines_store(
        make_routine, scripted):
    """A's op writes into B's own local store — the delivery shape a report already uses."""
    b = make_routine(slug="receiver")
    a = make_routine(slug="setter")
    set_capabilities(a, reminders="local")
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "add", "target": "receiver", "regex": "^shell: git push",
                               "description": "it publishes the moment it runs"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    held_by_b, _ = store.load_local(b)
    assert [(r.regex, r.owner, r.kind) for r in held_by_b] == [
        ("^shell: git push", "setter", "action")]
    # …and the setter did NOT take it on itself: a caution about B's actions cannot fire in A
    assert store.load_local(a)[0] == []
    assert "set rem-" in prompt_text(ep) and "on receiver" in prompt_text(ep)
    assert status == "ok"


def test_the_target_meets_a_foreign_hook_as_foreign_and_is_offered_the_disposition(
        make_routine, scripted):
    b = make_routine(slug="receiver2")
    set_capabilities(b, reminders="local")
    store.save_local(b, [_rem(rid="rem-fo", regex="^util:danger", owner="setter",
                              desc="setter says this wipes the shared store")], {})
    ep = scripted([util("danger"), write_file("state/x.txt", content="x"), finish()])
    status, run_dir = run_routine(b, server_for(b), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert len(holds) == 1
    assert holds[0]["payload"]["reminders"][0]["owner"] == "setter"
    shown = prompt_text(ep)
    assert "SET BY `setter`, not by you" in shown
    assert "keep" in shown and "mute" in shown and "remove" in shown
    assert status == "ok"


def test_the_targets_remove_disposition_deletes_the_hook_and_is_remembered(
        make_routine, scripted):
    b = make_routine(slug="receiver3")
    set_capabilities(b, reminders="local")
    store.save_local(b, [_rem(rid="rem-fo", regex="^util:danger", owner="setter",
                              desc="setter says this wipes the shared store")], {})
    ep = scripted([util("danger"),
                   {**write_file("state/x.txt", content="x"),
                    "remind_feedback": {"id": "rem-fo", "label": "could_not",
                                        "disposition": "remove"}},
                   finish()])
    status, _run_dir = run_routine(b, server_for(b), run_ts=TS)

    assert store.load_local(b)[0] == []                       # gone from the store
    assert store.load_refused(b) == [{"owner": "setter", "regex": "^util:danger",
                                      "kind": "action"}]      # …and remembered
    assert "REMOVED (set by setter)" in prompt_text(ep)
    assert status == "ok"


def test_the_owner_cannot_set_a_pattern_the_target_removed(make_routine, scripted):
    b = make_routine(slug="receiver4")
    a = make_routine(slug="setter4")
    set_capabilities(a, reminders="local")
    (b / "state").mkdir(parents=True, exist_ok=True)
    store.save_local(b, [], {}, [{"owner": "setter4", "regex": "^shell: git push",
                                  "kind": "action"}])
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "add", "target": "receiver4", "regex": "^shell: git push",
                               "description": "again"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    assert store.load_local(b)[0] == []                        # nothing was set
    assert "removed that hook before" in prompt_text(ep)
    assert status == "ok"


def test_a_muted_foreign_hook_survives_the_run_in_the_store(make_routine, scripted):
    """Mute is \"not this run\": the definition must still be there for its owner to see."""
    b = make_routine(slug="receiver5")
    set_capabilities(b, reminders="local")
    store.save_local(b, [_rem(rid="rem-fo", regex="^util:danger", owner="setter",
                              desc="setter's caution")], {})
    scripted([util("danger"),
              {**write_file("state/x.txt", content="x"),
               "remind_feedback": {"id": "rem-fo", "label": "could_not", "disposition": "mute"}},
              # a further definition write in the same run must not erase the muted record
              {**write_file("state/y.txt", content="y"),
               "remind": {"op": "add", "regex": "^util:other", "description": "mine"}},
              finish()])
    status, _run_dir = run_routine(b, server_for(b), run_ts=TS)

    held, _ = store.load_local(b)
    by_id = {r.id: r for r in held}
    assert "rem-fo" in by_id and by_id["rem-fo"].disposition == "mute"
    assert status == "ok"


def test_a_disposition_on_your_own_reminder_is_refused(make_routine, scripted):
    b = make_routine(slug="receiver6")
    set_capabilities(b, reminders="local")
    store.save_local(b, [_rem(rid="rem-own", regex="^util:danger", desc="my own caution")], {})
    ep = scripted([util("danger"),
                   {**write_file("state/x.txt", content="x"),
                    "remind_feedback": {"id": "rem-own", "label": "would_have",
                                        "disposition": "remove"}},
                   finish()])
    status, _run_dir = run_routine(b, server_for(b), run_ts=TS)

    assert [r.id for r in store.load_local(b)[0]] == ["rem-own"]      # still there
    assert "is your own reminder" in prompt_text(ep)
    assert status == "ok"


@pytest.mark.parametrize(("op", "fragment"), [
    ({"op": "add", "target": "no such slug!", "regex": "^util:x", "description": "d"},
     "kebab-case slug"),
    ({"op": "revise", "id": "rem-1", "target": "receiver7", "description": "d"},
     "belongs to `op=add`"),
    ({"op": "add", "target": "receiver7", "scope": "global", "reach": "universal",
      "regex": "^util:x", "description": "d"},
     "two different reaches"),
])
def test_the_target_field_is_checked_inside_the_schema_retry_cycle(tmp_path, op, fragment):
    from rsched.engine.actions import validate_action
    from rsched.policyload import load_policy

    policy = load_policy(tmp_path, [], {"reminders": "global"})
    problems = validate_action({"say": "s", "kind": "read_file", "path": "a.md", "remind": op},
                               grants=policy)
    assert problems and any(fragment in p for p in problems)


def test_setting_a_hook_on_a_routine_that_does_not_exist_is_reported_not_silent(
        make_routine, scripted):
    a = make_routine(slug="setter8")
    set_capabilities(a, reminders="local")
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "add", "target": "ghost-routine", "regex": "^util:x",
                               "description": "d"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)
    assert "no routine 'ghost-routine' is installed" in prompt_text(ep)
    assert status == "ok"
