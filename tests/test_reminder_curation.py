"""D231 — the curator may retire a reminder row in ANOTHER routine's store, running or not.

The operator's answer: "Give rules-review a way to delete/annotate a reminder row in a DISABLED
routine's store (an engine capability or a census verb), since no report can reach a routine that
never runs" — with a preference the design turns on: "prefer ANNOTATE-AS-DEAD over DELETE, so a
row's hold tally survives", because "a deletion loses the tally that proves the pattern was too
broad".

The four rows four consecutive curation passes (2026-10-29 … 2026-11-01) could not dispose of are
the fixtures here, tallies and all. The invariants under test:

- an annotated row KEEPS its whole tally and stops firing;
- the annotation says who retired it, when, and WHY;
- the holder's own later writes carry the annotation instead of dropping it;
- a routine without the curator's dial (`reminders: global`) is refused, and the refusal names
  what is missing;
- the target routine being `enabled: false` makes no difference — that is the whole point;
- `op=delete` with a `target` is still there, for a row with no evidence worth keeping.
"""

import yaml

from conftest import finish, util, write_file
from helpers import prompt_text, server_for, set_capabilities
from rsched import remind_dead
from rsched import reminders as store
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from test_reminders import TS

CURATOR = "rules-review"

#: The acceptance set — the four undeletable rows, with the tallies the passes recorded.
ACCEPTANCE = [
    ("voice-model-trainer", "rem-20260923-023351-1", "^util:remote train ",
     {"fires": 3, "could_not": 1}, "premise disproved at the source"),
    ("funscript-trainer", "rem-20260921-024619-1", "^util:remote ",
     {"fires": 22, "could_not": 8, "would_have": 6, "didnt": 4},
     "premise disproved at the source; 22 holds, 8 could_not"),
    ("birthday-admin", "rem-20260913-095300-1", "^util:mail send ",
     {"fires": 4, "didnt": 2}, "redundant with a shared reminder"),
    ("funscript-trainer", "rem-20261002-180000-1", "^script:nothing ",
     {}, "0 holds, 0 matches"),
]


def _row(rid, regex, stats=None, owner="", desc="it was written when the premise held"):
    return store.Reminder(id=rid, regex=regex, description=desc, scope="local", kind="action",
                          owner=owner, created_run="holder:20260923-020000",
                          stats={**store.blank_stats(), **(stats or {})})


def _holder(tmp_path, slug, rows, *, enabled=True):
    """A routine directory holding those rows — `enabled` is written as its config says it."""
    d = tmp_path / "routines" / slug
    (d / "state").mkdir(parents=True, exist_ok=True)
    d.joinpath("routine.yaml").write_text(
        yaml.safe_dump({"name": slug, "slug": slug, "enabled": enabled}), encoding="utf-8")
    store.save_local(d, rows, {})
    return d


# --- the store half: the tally survives, the row stops firing ------------------------------

def test_an_annotated_row_keeps_its_whole_tally(tmp_path):
    """The operator's reason for preferring annotate over delete: the tally IS the evidence."""
    rows = {"fires": 22, "could_not": 8, "would_have": 6, "didnt": 4}
    holder = _holder(tmp_path, "funscript-trainer", [_row("rem-20260921-024619-1", "^util:remote ",
                                                          rows)])
    marked = remind_dead.annotate(holder, "rem-20260921-024619-1", by=CURATOR,
                                  reason="premise disproved at the source",
                                  at="2026-11-02")
    assert marked is not None
    loaded, _ = store.load_local(holder)
    assert [r.id for r in loaded] == ["rem-20260921-024619-1"]      # the row is still there
    assert loaded[0].stats == {**store.blank_stats(), **rows}       # …with every count
    assert store.looks_too_broad(loaded[0].stats) is True           # …still readable as evidence


def test_an_annotated_row_stops_firing(tmp_path):
    holder = _holder(tmp_path, "voice-model-trainer", [_row("rem-20260923-023351-1",
                                                            "^util:remote train ")])
    home = tmp_path / "lib" / "reminders"
    run = "voice-model-trainer:20261102-150000"
    assert [r.id for r in store.active(holder, home, "local", run_id=run)] == [
        "rem-20260923-023351-1"]
    remind_dead.annotate(holder, "rem-20260923-023351-1", by=CURATOR, reason="disproved",
                         at="2026-11-02")
    assert store.active(holder, home, "local", run_id=run) == []


def test_the_annotation_records_who_when_and_why(tmp_path):
    holder = _holder(tmp_path, "birthday-admin", [_row("rem-20260913-095300-1",
                                                       "^util:mail send ")])
    remind_dead.annotate(holder, "rem-20260913-095300-1", by=CURATOR,
                         reason="redundant with a shared reminder", at="2026-11-02")
    mark = remind_dead.read_dead(holder)["rem-20260913-095300-1"]
    assert mark == {"by": CURATOR, "at": "2026-11-02",
                    "reason": "redundant with a shared reminder"}


def test_a_marker_without_a_reason_is_not_written(tmp_path):
    """A row retired with no stated reason is the one outcome worse than leaving it live."""
    holder = _holder(tmp_path, "birthday-admin", [_row("rem-1", "^util:x")])
    assert remind_dead.annotate(holder, "rem-1", by=CURATOR, reason="  ", at="2026-11-02") is None
    assert remind_dead.read_dead(holder) == {}


def test_annotating_a_row_the_store_does_not_hold_changes_nothing(tmp_path):
    holder = _holder(tmp_path, "birthday-admin", [_row("rem-1", "^util:x")])
    assert remind_dead.annotate(holder, "rem-nope", by=CURATOR, reason="r", at="x") is None
    assert remind_dead.read_dead(holder) == {}


def test_the_holders_own_later_write_carries_the_annotation(tmp_path):
    """`save_local` rewrites the whole file: a key it dropped would resurrect the dead row."""
    holder = _holder(tmp_path, "funscript-trainer", [_row("rem-20260921-024619-1", "^util:remote ",
                                                          {"fires": 22, "could_not": 8})])
    remind_dead.annotate(holder, "rem-20260921-024619-1", by=CURATOR, reason="disproved",
                         at="2026-11-02")
    kept, gstats = store.load_local(holder)
    store.save_local(holder, [*kept, _row("rem-new", "^util:other")], gstats)
    assert "rem-20260921-024619-1" in remind_dead.read_dead(holder)
    home = tmp_path / "lib" / "reminders"
    assert [r.id for r in store.active(holder, home, "local", run_id="r:1")] == ["rem-new"]


def test_a_dead_row_is_decorated_for_the_console_so_it_does_not_read_as_live(tmp_path):
    holder = _holder(tmp_path, "birthday-admin", [_row("rem-20260913-095300-1", "^util:mail send ")])
    remind_dead.annotate(holder, "rem-20260913-095300-1", by=CURATOR, reason="redundant",
                         at="2026-11-02")
    rows, _ = store.load_local(holder)
    shown = remind_dead.decorate(holder, rows)
    assert shown[0]["id"] == "rem-20260913-095300-1"
    assert shown[0]["dead"]["reason"] == "redundant"


def test_deleting_an_already_dead_row_takes_its_annotation_with_it(tmp_path):
    """An orphan marker keyed to an id no row has would be a store lying about its history."""
    holder = _holder(tmp_path, "funscript-trainer", [_row("rem-a", "^util:x"),
                                                     _row("rem-b", "^util:y")])
    remind_dead.annotate(holder, "rem-a", by=CURATOR, reason="disproved", at="2026-11-02")
    remind_dead.annotate(holder, "rem-b", by=CURATOR, reason="also disproved", at="2026-11-02")
    kept, gstats = store.load_local(holder)
    store.save_local(holder, [r for r in kept if r.id != "rem-a"], gstats)
    remind_dead.forget(holder, "rem-a")
    assert set(remind_dead.read_dead(holder)) == {"rem-b"}


# --- the engine half: one curating run disposes of a row in a store it does not own --------

def _curator(make_routine, *, dial="global"):
    a = make_routine(slug=CURATOR)
    set_capabilities(a, reminders=dial)
    return a


def test_a_curating_run_marks_a_row_dead_in_another_routines_store(make_routine, scripted,
                                                                   tmp_path):
    a = _curator(make_routine)
    holder = _holder(tmp_path, "funscript-trainer",
                     [_row("rem-20260921-024619-1", "^util:remote ",
                           {"fires": 22, "could_not": 8, "would_have": 6, "didnt": 4})])
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "dead", "target": "funscript-trainer",
                               "id": "rem-20260921-024619-1",
                               "description": "premise disproved at the source"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    held, _ = store.load_local(holder)
    assert [r.id for r in held] == ["rem-20260921-024619-1"]
    assert held[0].stats["fires"] == 22 and held[0].stats["could_not"] == 8
    mark = remind_dead.read_dead(holder)["rem-20260921-024619-1"]
    assert mark["by"] == CURATOR and mark["reason"] == "premise disproved at the source"
    assert "marked DEAD" in prompt_text(ep)
    assert status == "ok"


def test_a_disabled_target_makes_no_difference(make_routine, scripted, tmp_path):
    """The whole point: no report can reach a routine that never runs."""
    a = _curator(make_routine)
    holder = _holder(tmp_path, "voice-model-trainer",
                     [_row("rem-20260923-023351-1", "^util:remote train ", {"fires": 3})],
                     enabled=False)
    assert yaml.safe_load((holder / "routine.yaml").read_text())["enabled"] is False
    scripted([{**write_file("state/x.txt", content="x"),
               "remind": {"op": "dead", "target": "voice-model-trainer",
                          "id": "rem-20260923-023351-1",
                          "description": "premise disproved at the source"}},
              finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    assert "rem-20260923-023351-1" in remind_dead.read_dead(holder)
    assert store.load_local(holder)[0][0].stats["fires"] == 3
    assert status == "ok"


def test_a_routine_without_the_curator_dial_is_refused_by_name(make_routine, scripted, tmp_path):
    a = _curator(make_routine, dial="local")
    holder = _holder(tmp_path, "birthday-admin", [_row("rem-20260913-095300-1",
                                                       "^util:mail send ")])
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "dead", "target": "birthday-admin",
                               "id": "rem-20260913-095300-1",
                               "description": "redundant with a shared reminder"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    assert remind_dead.read_dead(holder) == {}          # nothing was annotated
    shown = prompt_text(ep)
    assert "curator's setting" in shown and "reminders:global" in shown
    assert status == "ok"


def test_delete_with_a_target_removes_a_row_with_no_evidence_worth_keeping(make_routine,
                                                                           scripted, tmp_path):
    a = _curator(make_routine)
    holder = _holder(tmp_path, "funscript-trainer", [_row("rem-20261002-180000-1",
                                                          "^script:nothing ")])
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "delete", "target": "funscript-trainer",
                               "id": "rem-20261002-180000-1",
                               "description": "0 holds, 0 matches"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    assert store.load_local(holder)[0] == []
    assert "deleted" in prompt_text(ep)
    assert status == "ok"


def test_a_row_the_target_does_not_hold_is_reported_not_silent(make_routine, scripted, tmp_path):
    a = _curator(make_routine)
    _holder(tmp_path, "birthday-admin", [_row("rem-1", "^util:x")])
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "dead", "target": "birthday-admin", "id": "rem-nope",
                               "description": "whatever"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)
    assert "does not hold" in prompt_text(ep)
    assert status == "ok"


def test_a_target_routine_that_is_not_installed_is_reported(make_routine, scripted):
    a = _curator(make_routine)
    ep = scripted([{**write_file("state/x.txt", content="x"),
                    "remind": {"op": "dead", "target": "no-such-routine", "id": "rem-1",
                               "description": "r"}},
                   finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)
    assert "no routine 'no-such-routine' is installed" in prompt_text(ep)
    assert status == "ok"


def test_the_whole_acceptance_set_is_disposed_of_in_one_run(make_routine, scripted, tmp_path):
    """The four rows the passes of 2026-10-29 … 2026-11-01 could not touch."""
    a = _curator(make_routine)
    holders = {}
    for slug, rid, regex, stats, _why in ACCEPTANCE:
        rows = [_row(rid, regex, stats)]
        if slug in holders:
            existing, _ = store.load_local(holders[slug])
            rows = [*existing, *rows]
        holders[slug] = _holder(tmp_path, slug, rows)
    replies = [{**write_file(f"state/{n}.txt", content="x"),
                "remind": {"op": "delete" if not stats else "dead", "target": slug, "id": rid,
                           "description": why}}
               for n, (slug, rid, _regex, stats, why) in enumerate(ACCEPTANCE)]
    scripted([*replies, finish()])
    status, _run_dir = run_routine(a, server_for(a), run_ts=TS)

    for slug, rid, _regex, stats, why in ACCEPTANCE:
        held = {r.id: r for r in store.load_local(holders[slug])[0]}
        if not stats:                                   # 0 holds, 0 matches — deleted outright
            assert rid not in held
            continue
        assert rid in held and held[rid].stats == {**store.blank_stats(), **stats}
        assert remind_dead.read_dead(holders[slug])[rid]["reason"] == why
    assert status == "ok"


def test_a_dead_row_does_not_hold_the_holders_own_action(make_routine, scripted):
    """Not matched, not held, not tallied — in the holder's own run, which is where it fired."""
    b = make_routine(slug="holder")
    set_capabilities(b, reminders="local")
    store.save_local(b, [_row("rem-20260921-024619-1", "^util:danger", {"fires": 22,
                                                                        "could_not": 8})], {})
    remind_dead.annotate(b, "rem-20260921-024619-1", by=CURATOR, reason="disproved",
                         at="2026-11-02")
    scripted([util("danger"), finish()])
    status, run_dir = run_routine(b, server_for(b), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    assert [e for e in events if e["type"] == "observation"
            and e["payload"].get("kind") == "reminder_hold"] == []
    held, _ = store.load_local(b)
    assert held[0].stats["fires"] == 22                  # not even a fire was counted
    assert status == "ok"


# --- the grammar, checked inside the schema-retry cycle ------------------------------------

def test_the_dead_op_needs_a_target_an_id_and_a_reason(tmp_path):
    from rsched.engine.actions import validate_action
    from rsched.policyload import load_policy

    policy = load_policy(tmp_path, [], {"reminders": "global"})
    for op, fragment in (
            ({"op": "dead", "id": "rem-1", "description": "r"}, "needs the `target`"),
            ({"op": "dead", "target": "holder", "description": "r"}, "needs the `id`"),
            ({"op": "dead", "target": "holder", "id": "rem-1"}, "`description`"),
            ({"op": "revise", "target": "holder", "id": "rem-1", "description": "r"},
             "belongs to"),
    ):
        problems = validate_action({"say": "s", "kind": "read_file", "path": "a.md",
                                    "remind": op}, grants=policy)
        assert problems and any(fragment in p for p in problems), (op, problems)
