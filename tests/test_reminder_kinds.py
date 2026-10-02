"""D152-C step 1 — the reminder's TRIGGER is a named kind, not always the action string.

Today every reminder matches the canonical ACTION rendering before the action runs. The
operator's decision (D152, option C, 2026-09-27) is that a hook may also watch what came BACK
(`result`), what the model SAID that turn (`prose`), and the special turns (`turn:first`,
`turn:finish`, `turn:question`, `turn:answer`, `turn:error`).

The regression that matters most is the first class: every reminder written before this change
has no `kind` field at all, reads as `action`, and must behave exactly as it did.
"""

import json

import pytest

from conftest import finish, util, write_file
from helpers import prompt_text, server_for, set_capabilities
from rsched import reminder_checks as checks
from rsched import reminders as store
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.policyload import load_policy
from rsched.reminders import Reminder
from test_reminders import TS


def _rem(rid="rem-1", regex="^util:danger", desc="it deletes the target", kind="action",
         scope="local", reach="", **stats):
    return Reminder(id=rid, regex=regex, description=desc, scope=scope, kind=kind,
                    created_run="r:1", stats={**store.blank_stats(), **stats},
                    reach=reach or ("universal" if scope == "global" else ""))


# --- the store knows the kinds ------------------------------------------------------------

def test_action_is_the_default_kind_so_every_stored_reminder_keeps_working(tmp_path):
    """A record written before this change carries no `kind`; it must read as `action`."""
    path = store.local_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reminders": [
        {"id": "rem-old", "regex": "^util:danger", "description": "d",
         "created_run": "r:0", "stats": store.blank_stats()}]}), encoding="utf-8")
    loaded, _ = store.load_local(tmp_path)
    assert [r.kind for r in loaded] == ["action"]


def test_a_kind_round_trips_through_the_local_store(tmp_path):
    store.save_local(tmp_path, [_rem(rid="rem-r", regex="exit 2", kind="result")], {})
    loaded, _ = store.load_local(tmp_path)
    assert (loaded[0].kind, loaded[0].regex) == ("result", "exit 2")


def test_a_record_with_an_unknown_kind_reads_as_action_rather_than_breaking_the_run(tmp_path):
    path = store.local_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reminders": [
        {"id": "rem-x", "regex": "^util:danger", "description": "d", "kind": "nonsense",
         "created_run": "r:0", "stats": store.blank_stats()}]}), encoding="utf-8")
    loaded, _ = store.load_local(tmp_path)
    assert [r.kind for r in loaded] == ["action"]


def test_matching_selects_by_kind(tmp_path):
    live = [_rem(rid="rem-a", regex="danger", kind="action"),
            _rem(rid="rem-r", regex="danger", kind="result")]
    assert [r.id for r in store.matching(live, "util:danger")] == ["rem-a"]
    assert [r.id for r in store.matching(live, "util:danger", kind="result")] == ["rem-r"]


def test_the_union_dedupes_by_regex_and_kind(tmp_path):
    """Two hooks with the same pattern on different kinds are different consequence classes."""
    store.save_local(tmp_path, [_rem(rid="rem-l", regex="^util:danger", kind="result")], {})
    home = tmp_path / "lib" / "reminders"
    home.mkdir(parents=True)
    store.write_global(home, _rem(rid="rem-g", regex="^util:danger", kind="action",
                                  scope="global", reach="universal"))
    live = store.active(tmp_path, home, "local")
    assert sorted(r.id for r in live) == ["rem-g", "rem-l"]


# --- the write gate ------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["action", "result", "prose", "turn:first", "turn:finish",
                                  "turn:question", "turn:answer", "turn:error"])
def test_every_declared_kind_passes_the_write_gate(kind):
    assert checks.kind_problem(kind) is None


@pytest.mark.parametrize("kind", ["", "observation", "turn", "turn:middle", "Action", 7])
def test_an_undeclared_kind_is_refused_with_the_list(kind):
    problem = checks.kind_problem(kind)
    assert problem and "remind.kind" in problem


def test_a_non_action_kind_is_not_held_to_the_canonical_action_forms():
    """`canon_problem` exists because a pattern anchored on a rendering no ACTION produces can
    never fire. An observation or a `say` is not an action string, so that check must not
    apply to it — otherwise `^exit 2` is refused for naming no action kind."""
    assert checks.regex_problem("^util fs-ops", kind="action")
    assert checks.regex_problem("^util fs-ops", kind="result") is None


def test_the_add_op_accepts_a_kind_and_refuses_an_unknown_one(tmp_path):
    policy = load_policy(tmp_path, [], {"reminders": "local"})
    base = {"say": "s", "kind": "read_file", "path": "a.md",
            "remind": {"op": "add", "regex": "exit 2", "description": "the gate went red",
                       "kind": "result"}}
    assert validate_action(base, grants=policy) == []
    bad = {**base, "remind": {**base["remind"], "kind": "observation"}}
    assert any("remind.kind" in p for p in validate_action(bad, grants=policy))


# --- the new fire points -------------------------------------------------------------------

def test_a_result_hook_fires_on_the_observation_and_does_not_hold_the_action(
        make_routine, scripted):
    """The distinguishing test of step 1: the action RUNS (a result is only knowable after it
    did), and the caution rides the observation at no turn cost — no `reminder_hold` event."""
    d = make_routine(slug="remkind")
    set_capabilities(d, reminders="local")
    # the target is the OBSERVATION as the model is shown it — "wrote N bytes to <path>"
    store.save_local(d, [_rem(rid="rem-res", regex=r"wrote \d+ bytes to state/probe",
                              kind="result",
                              desc="the probe wrote the sibling routine's input")], {})
    ep = scripted([write_file("state/probe.txt", content="probe-one"), finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert holds == []                                   # nothing was held
    assert (d / "state" / "probe.txt").read_text(encoding="utf-8") == "probe-one"  # it RAN
    shown = prompt_text(ep)
    assert "the probe wrote the sibling routine's input" in shown
    assert store.load_local(d)[0][0].stats["fires"] == 1
    assert status == "ok"


def test_a_result_hook_does_not_fire_on_the_action_that_produced_it(make_routine, scripted):
    """A pattern that would match the ACTION rendering must stay silent at kind=result: the
    two targets are different subjects, and conflating them is the bug this step prevents."""
    d = make_routine(slug="remkind2")
    set_capabilities(d, reminders="local")
    store.save_local(d, [_rem(rid="rem-res2", regex=r"^util:danger", kind="result",
                              desc="should never fire — no observation renders that way")], {})
    scripted([util("danger"), write_file("state/x.txt", content="x"), finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    kinds = [e["payload"].get("kind") for e in events if e["type"] == "observation"]
    assert "reminder_hold" not in kinds
    assert store.load_local(d)[0][0].stats["fires"] == 0
    assert status == "ok"


def test_a_prose_hook_matches_what_the_model_said_that_turn(make_routine, scripted):
    d = make_routine(slug="remprose")
    set_capabilities(d, reminders="local")
    store.save_local(d, [_rem(rid="rem-pr", regex="force.push", kind="prose",
                              desc="a force push rewrites what the remote already served")], {})
    ep = scripted([{**write_file("state/y.txt", content="y"),
                    "say": "I will force push after this"}, finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert "a force push rewrites what the remote already served" in prompt_text(ep)
    assert store.load_local(d)[0][0].stats["fires"] == 1
    assert status == "ok"


def test_an_action_kind_hook_still_holds_before_execution(make_routine, scripted):
    """The regression guard: everything the layer did before this change, unchanged."""
    d = make_routine(slug="remsame")
    set_capabilities(d, reminders="local")
    store.save_local(d, [_rem(rid="rem-act", regex=r"^write_file path=state/probe",
                              desc="it clobbers the sibling run's input")], {})
    scripted([write_file("state/probe.txt", content="one"),
              write_file("state/probe.txt", content="one"), finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    holds = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "reminder_hold"]
    assert len(holds) == 1
    assert (d / "state" / "probe.txt").read_text(encoding="utf-8") == "one"
    assert status == "ok"
