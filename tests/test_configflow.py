"""Mid-run config changes as in-flow messages (F337).

The complaint this closes: a config edit made while a run is live reached it for SOME fields and
silently not for others, and the run was never told either way — so "I changed it while it was
running" had two different meanings depending on which field was touched.

The anti-drift guard is `test_every_patch_field_declares_its_half`: a new config field cannot be
added without declaring which half it is in, so the divergence cannot quietly come back.
"""

from __future__ import annotations

import pytest

from rsched import configflow
from rsched.web.api_conversations import ConversationPatch
from rsched.web.api_routine_edit import RoutinePatch


def test_every_patch_field_declares_its_half():
    """THE point of the module. A field the table does not know is a field whose live behaviour
    nobody decided — which is exactly how the drift happened the first time."""
    declared = set(configflow.CLASSIFICATION)
    for model in (RoutinePatch, ConversationPatch):
        missing = sorted(set(model.model_fields) - declared)
        assert not missing, (
            f"{model.__name__} has undeclared config field(s) {missing} — add each to "
            "configflow.CLASSIFICATION as LIVE (the engine adopts it at a turn boundary) or "
            "NEXT_RUN, with the reason the run is shown")


def test_every_classification_names_a_half_and_a_reason():
    for field, (half, why) in configflow.CLASSIFICATION.items():
        assert half in (configflow.LIVE, configflow.NEXT_RUN), field
        # the reason is not decoration — the operator reads it in the message
        assert why and len(why) > 20, f"{field}: give a real reason, not {why!r}"


def test_classify_splits_the_two_halves_and_flags_the_undeclared():
    live, later, unknown = configflow.classify(
        ["budgets", "models", "deliberation", "schedule", "invented"])
    assert live == ["budgets", "deliberation"]
    assert later == ["models", "schedule"]
    # an undeclared field reads as UNKNOWN, never silently as next-run — that swallow is the
    # failure mode in miniature
    assert unknown == ["invented"]


def test_change_note_names_both_halves_with_reasons():
    note = configflow.change_note(
        ["budgets", "models"], {"budgets": {"max_turns": 80}, "models": {"main": "gpt"}})
    assert "IN EFFECT NOW" in note and "NEXT RUN" in note
    assert "budgets" in note.split("NEXT RUN")[0]        # the live half, first
    assert "models" in note.split("NEXT RUN")[1]
    assert "{'max_turns': 80}" in note                   # the actual new value, not just a name
    assert "re-derived from the new ceilings" in note    # ...and why it is in that half


def test_change_note_is_empty_when_nothing_changed():
    assert configflow.change_note([], {}) == ""


def test_change_note_says_so_for_an_undeclared_field():
    note = configflow.change_note(["invented"], {})
    assert "not classified" in note and "invented" in note


def test_adoptable_is_exactly_the_live_half():
    assert set(configflow.ADOPTABLE) == {
        f for f, (half, _) in configflow.CLASSIFICATION.items() if half == configflow.LIVE}


# ---- the engine half: what a live run actually does with the signal --------------------------

def _loop(make_routine, tmp_path):
    from rsched.config import ServerConfig, load_routine
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript

    d = make_routine(slug="cfgr")
    cfg, _ = load_routine(d)
    run_dir = d / "runs" / "20260827-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260827-070000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.deliberation = "standard"

    class _Loop:
        def __init__(self):
            self.ctx = ctx
            self.messages: list[dict] = []
            self._last_config_ts = ""

    return _Loop(), run_dir


def _signal(run_dir, fields, values, ts="2026-08-27T07:05:00+02:00"):
    from rsched.paths import atomic_write_json
    atomic_write_json(run_dir / "control.json",
                      {"config_change": {"fields": fields, "values": values, "ts": ts}})


def test_live_run_adopts_the_live_half_and_is_told_about_both(make_routine, tmp_path):
    from rsched.engine.switches import apply_config_change

    loop, run_dir = _loop(make_routine, tmp_path)
    before = loop.ctx.budgets.max_turns
    _signal(run_dir, ["budgets", "deliberation", "models"],
            {"budgets": {"max_turns": before + 40}, "deliberation": "think-on-paper",
             "models": {"main": "other"}})
    apply_config_change(loop)

    # adopted, at this boundary
    assert loop.ctx.budgets.max_turns == before + 40
    assert loop.ctx.deliberation == "think-on-paper"
    # and TOLD — about the fields that waited as much as the ones that landed
    note = loop.messages[-1]["content"]
    assert "ENGINE NOTE" in note
    assert "IN EFFECT NOW" in note and "budgets" in note and "deliberation" in note
    assert "NEXT RUN" in note and "models" in note


def test_the_signal_is_edge_triggered_not_re_fired_every_turn(make_routine, tmp_path):
    """Same discipline as the model/deliberation/rule switches — otherwise every turn boundary
    re-injects the same note for the rest of the run."""
    from rsched.engine.switches import apply_config_change

    loop, run_dir = _loop(make_routine, tmp_path)
    _signal(run_dir, ["budgets"], {"budgets": {"max_turns": 99}})
    apply_config_change(loop)
    apply_config_change(loop)
    apply_config_change(loop)
    assert len(loop.messages) == 1
    assert loop.ctx.budgets.max_turns == 99


def test_a_later_change_fires_again(make_routine, tmp_path):
    from rsched.engine.switches import apply_config_change

    loop, run_dir = _loop(make_routine, tmp_path)
    _signal(run_dir, ["budgets"], {"budgets": {"max_turns": 99}})
    apply_config_change(loop)
    _signal(run_dir, ["budgets"], {"budgets": {"max_turns": 120}},
            ts="2026-08-27T07:09:00+02:00")
    apply_config_change(loop)
    assert len(loop.messages) == 2
    assert loop.ctx.budgets.max_turns == 120


def test_the_change_lands_in_the_transcript_not_only_in_the_prompt(make_routine, tmp_path):
    """It must be a visible in-flow event, never a second invisible mutation path."""
    import json

    from rsched.engine.switches import apply_config_change

    loop, run_dir = _loop(make_routine, tmp_path)
    _signal(run_dir, ["deliberation"], {"deliberation": "brief"})
    apply_config_change(loop)
    events = [json.loads(x) for x in
              (run_dir / "transcript.jsonl").read_text().splitlines() if x.strip()]
    injected = [e for e in events if e.get("type") == "user_injection"]
    assert len(injected) == 1
    assert "configuration while you are running" in injected[0]["payload"]["text"]


def test_a_junk_value_does_not_kill_a_live_run(make_routine, tmp_path):
    """Best-effort per field: a value the run cannot use is the operator's to see on the page,
    never a crash mid-run — and the note still says the field changed."""
    from rsched.engine.switches import apply_config_change

    loop, run_dir = _loop(make_routine, tmp_path)
    before = loop.ctx.budgets.max_turns
    _signal(run_dir, ["budgets", "deliberation"],
            {"budgets": {"max_turns": "not a number", "nonsense_key": 3},
             "deliberation": "no-such-level"})
    apply_config_change(loop)
    assert loop.ctx.budgets.max_turns == before          # unchanged, not corrupted
    assert loop.ctx.deliberation == "standard"           # an unknown level is ignored
    assert "budgets" in loop.messages[-1]["content"]     # but the run is still told


def test_no_signal_is_a_no_op(make_routine, tmp_path):
    from rsched.engine.switches import apply_config_change

    loop, _run_dir = _loop(make_routine, tmp_path)
    apply_config_change(loop)
    assert loop.messages == []


# ---- the web half ---------------------------------------------------------------------------

def test_patch_tells_a_live_run_and_stays_quiet_otherwise(api_client, make_routine):
    """The emitter is a no-op with nothing running — a config edit between runs needs no note,
    the next boot reads the file."""
    from rsched.paths import atomic_write_json, read_json

    c, _tmp = api_client
    d = make_routine(slug="livecfg")
    r = c.patch("/api/routines/livecfg", json={"budgets": {"max_turns": 55}})
    assert r.status_code == 200 and "told_live_run" not in r.json()

    run_dir = d / "runs" / "20260827-070000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "livecfg:20260827-070000", "state": "running", "turn": 2})
    r = c.patch("/api/routines/livecfg", json={"budgets": {"max_turns": 88}, "improve": False})
    assert r.status_code == 200 and r.json()["told_live_run"] is True
    sig = read_json(run_dir / "control.json")["config_change"]
    assert sorted(sig["fields"]) == ["budgets", "improve"]
    assert sig["values"]["budgets"] == {"max_turns": 88}
    assert sig["ts"]


@pytest.mark.parametrize("field", ["budgets", "deliberation", "grants"])
def test_the_designed_live_fields_are_classified_live(field):
    """The three F337 names as adoptable. Pinned so a later edit cannot quietly demote one."""
    assert configflow.CLASSIFICATION[field][0] == configflow.LIVE


@pytest.mark.parametrize("field", ["models", "schedule", "workflow"])
def test_the_designed_next_run_fields_are_classified_next_run(field):
    assert configflow.CLASSIFICATION[field][0] == configflow.NEXT_RUN
