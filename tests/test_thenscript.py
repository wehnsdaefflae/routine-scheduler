"""`then_script`: a write or edit and the script that checks it, in ONE action.

Of 3,459 write_file/edit_file actions in 160 fleet runs, 1,089 were followed directly by a run of
the file just changed — 536 of them a `script` — each a whole model turn re-reading ~175k tokens
of context. The riding script must pass exactly the gates it would pass alone: these tests hold
the validation, the hold seam, the skip on a failed change and the single observation to that.
"""

from __future__ import annotations

from conftest import finish
from helpers import prompt_text, reminder, server_for
from rsched import reminders as store
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.kindsurface import schema_for_kinds
from rsched.engine.observations import format_observation, is_failure
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from test_scripts import SCRIPT

TS = "20261008-090000"


def _write_then(argv, path="scripts/probe.py", content=SCRIPT):
    return {"say": "Write the probe and run it.", "kind": "write_file", "path": path,
            "content": content, "then_script": argv}


def _run(make_routine, scripted, monkeypatch, replies, *, result=(0, "ran", ""), local=()):
    d = make_routine(slug="fuser")
    if local:
        store.save_local(d, list(local), {})
    calls: list[tuple] = []
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda rd, name, args, **_kw: calls.append((name, list(args))) or result)
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d, ep, status, events, calls


def _observations(events) -> list[dict]:
    return [e["payload"] for e in events if e["type"] == "observation"]


# ---- the contract ------------------------------------------------------------------------------

def test_a_write_or_edit_may_carry_a_script_and_nothing_else_may():
    assert validate_action(_write_then(["probe", "--json"])) == []
    assert validate_action({"say": "s", "kind": "edit_file", "path": "a.md", "anchor": "x",
                            "replacement": "y", "then_script": ["probe"]}) == []
    stray = validate_action({"say": "s", "kind": "read_file", "path": "a.md",
                             "then_script": ["probe"]})
    assert stray and "then_script" in stray[0]


def test_a_malformed_argv_is_refused_with_its_shape():
    for bad in ([], [""], [3], "probe"):
        problems = validate_action(_write_then(bad))
        assert any('["<script-name>", ...args]' in p for p in problems), bad


def test_the_riding_script_is_judged_as_the_script_it_is():
    """A recipe whose `tools:` leave `script` out refuses the field with the reason a bare
    `script` would get — the write is not a way round the allowlist."""
    problems = validate_action(_write_then(["probe"]), allowed_kinds={"write_file"})
    assert problems and problems[0].startswith("then_script: kind=script is not available")
    assert validate_action(_write_then(["probe"]), allowed_kinds={"write_file", "script"}) == []


def test_the_field_is_shown_only_where_scripts_can_run():
    assert "then_script" in ACTION_SCHEMA["properties"]
    assert "then_script" not in schema_for_kinds(["write_file", "edit_file"])["properties"]
    shown = schema_for_kinds(["write_file", "script"])["properties"]
    assert shown["then_script"]["description"].startswith("write_file/edit_file: OPTIONAL")


# ---- the engine path ---------------------------------------------------------------------------

def test_the_change_and_its_script_take_one_turn(make_routine, scripted, monkeypatch):
    d, ep, status, events, calls = _run(make_routine, scripted, monkeypatch,
                                        [_write_then(["probe", "--json"]), finish()])
    assert status == "ok"
    assert (d / "scripts" / "probe.py").read_text(encoding="utf-8") == SCRIPT
    assert calls == [("probe", ["--json"])]
    obs = next(o for o in _observations(events) if o.get("kind") == "write_file")
    assert obs["then_script"]["exit"] == 0 and obs["then_script"]["stdout"] == "ran"
    assert sum(e["type"] == "assistant_action" for e in events) == 2   # write+run, then finish
    shown = prompt_text(ep)
    assert "OBSERVATION (write_file): wrote" in shown
    assert "OBSERVATION (script probe, exit 0)" in shown


def test_a_change_that_did_not_land_runs_no_script(make_routine, scripted, monkeypatch):
    edit = {"say": "Fix the probe and rerun it.", "kind": "edit_file", "path": "notes.md",
            "anchor": "no such text", "replacement": "y", "then_script": ["probe"]}
    _d, ep, _status, events, calls = _run(make_routine, scripted, monkeypatch, [edit, finish()])
    assert calls == [], "a failed edit must not run the script that would check it"
    obs = next(o for o in _observations(events) if o.get("kind") == "edit_file")
    assert obs["then_script_skipped"] and "then_script" not in obs
    assert is_failure(obs)
    assert "[then_script NOT run]" in prompt_text(ep)


def test_a_failing_script_keeps_the_change_and_fails_the_action(make_routine, scripted,
                                                                 monkeypatch):
    d, _ep, _status, events, _calls = _run(make_routine, scripted, monkeypatch,
                                           [_write_then(["probe"]), finish()],
                                           result=(1, "", "Traceback: boom"))
    assert (d / "scripts" / "probe.py").exists(), "the change stands; only its check failed"
    obs = next(o for o in _observations(events) if o.get("kind") == "write_file")
    assert is_failure(obs)
    text = format_observation(obs)
    assert "OBSERVATION (script probe, exit 1)" in text and "Traceback: boom" in text


def test_a_reminder_on_the_script_holds_the_whole_fused_action(make_routine, scripted,
                                                               monkeypatch):
    """A caution the run left itself about `script:probe` must hold the write that carries it
    as surely as a bare call — and say the write did not run either. Re-emitting the same
    action is the confirmation, exactly as for any held action."""
    caution = "probe posts to the live endpoint"
    _d, ep, status, events, calls = _run(
        make_routine, scripted, monkeypatch,
        [_write_then(["probe"]), _write_then(["probe"]), finish()],
        local=[reminder(rid="rem-s", regex=r"^script:probe", desc=caution)])
    holds = [o for o in _observations(events) if o.get("kind") == "reminder_hold"]
    assert len(holds) == 1
    assert holds[0]["action"] == "script:probe"
    assert holds[0]["rides"] == "write_file path=scripts/probe.py"
    assert calls == [("probe", [])], "held once, then the identical action ran both parts"
    assert status == "ok"
    assert "which did not run either" in prompt_text(ep)
