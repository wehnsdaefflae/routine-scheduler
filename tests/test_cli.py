"""CLI helpers: _render_event (transcript event → console line for `rsched run-once`) and
_parse_model_overrides (--model kind=name). Driven with plain dicts — no engine, no network.
Assertions target information content (what a reader needs on the line), not exact layout."""

import pytest

from rsched.cli import _parse_model_overrides, _render_event

# ---------------------------------------------------------------- _render_event


def test_render_header_names_run_and_orchestrator():
    line = _render_event({"type": "header", "run_id": "radar:20260715-070000",
                          "orchestrator": {"endpoint": "openrouter", "model": "glm-5.2"}})
    assert "radar:20260715-070000" in line and "openrouter:glm-5.2" in line


def test_render_assistant_action_carries_turn_say_and_kind_brief():
    line = _render_event({"type": "assistant_action", "turn": 7,
                          "payload": {"kind": "write_file", "say": "Recording the result.",
                                      "path": "state/out.md", "content": "x"}})
    assert "7" in line and "Recording the result." in line
    assert "write_file" in line and "state/out.md" in line

    util_line = _render_event({"type": "assistant_action", "turn": 1,
                               "payload": {"kind": "util", "say": "s", "name": "websearch",
                                           "args": ["climate", "--json"]}})
    assert "websearch" in util_line and "climate --json" in util_line

    spawn_line = _render_event({"type": "assistant_action", "turn": 2,
                                "payload": {"kind": "spawn", "say": "s", "label": "research",
                                            "workflow": "general-task", "prompt": "P"}})
    assert "research" in spawn_line and "general-task" in spawn_line

    ask_line = _render_event({"type": "assistant_action", "turn": 3,
                              "payload": {"kind": "ask_user", "say": "s",
                                          "question": "Ship it?", "mode": "blocking"}})
    assert "Ship it?" in ask_line

    fin_line = _render_event({"type": "assistant_action", "turn": 4,
                              "payload": {"kind": "finish", "say": "s", "status": "partial"}})
    assert "finish" in fin_line and "partial" in fin_line


def test_render_observation_variants():
    assert "exit 0" in _render_event({"type": "observation", "payload": {
        "kind": "util", "name": "websearch", "exit": 0}})
    assert "missing" in _render_event({"type": "observation", "payload": {
        "kind": "util", "name": "ghost", "missing": True}})

    wu = {"kind": "write_util", "name": "adder"}
    assert "pending approval" in _render_event(
        {"type": "observation", "payload": {**wu, "pending_approval": True}})
    assert "declined" in _render_event(
        {"type": "observation", "payload": {**wu, "declined": True}})
    assert "selftest ok" in _render_event(
        {"type": "observation", "payload": {**wu, "selftest_ok": True}})
    assert "selftest failed" in _render_event(
        {"type": "observation", "payload": {**wu, "selftest_ok": False}})

    assert "(error)" in _render_event({"type": "observation",
                                       "payload": {"kind": "llm", "error": "boom"}})

    assert "REJECTED" in _render_event({"type": "observation", "payload": {
        "kind": "spawn", "rejected": True, "reason": "parallel cap"}})
    assert "#3" in _render_event({"type": "observation", "payload": {"kind": "spawn", "n": 3}})
    sub = _render_event({"type": "observation",
                         "payload": {"kind": "subtask", "n": 2, "started": True}})
    assert "#2" in sub and "sequential" in sub
    assert "cap" in _render_event({"type": "observation", "payload": {
        "kind": "subtask", "rejected": True, "reason": "cap"}})

    waited = _render_event({"type": "observation", "payload": {
        "kind": "wait", "finished": [{"n": 1, "status": "ok"}]}})
    assert "#1:ok" in waited
    assert "timeout" in _render_event({"type": "observation", "payload": {
        "kind": "wait", "finished": [], "timed_out": True}})
    assert "nothing new" in _render_event({"type": "observation", "payload": {
        "kind": "wait", "finished": [], "timed_out": False}})

    # any other observation kind still renders a line naming the kind
    assert "read_file" in _render_event({"type": "observation",
                                         "payload": {"kind": "read_file", "path": "f"}})


def test_render_dialog_and_lifecycle_events():
    q = _render_event({"type": "question",
                       "payload": {"mode": "blocking", "question": "Which city?"}})
    assert "blocking" in q and "Which city?" in q
    assert "yes, go" in _render_event({"type": "answer", "payload": {"text": "yes, go"}})
    assert "also the moon" in _render_event({"type": "user_injection",
                                             "payload": {"text": "also the moon"}})
    err = _render_event({"type": "error",
                         "payload": {"where": "schema", "message": "not a valid action"}})
    assert "schema" in err and "not a valid action" in err
    comp = _render_event({"type": "compaction",
                          "payload": {"before_chars": 90000, "after_chars": 12000}})
    assert "90000" in comp and "12000" in comp
    fin = _render_event({"type": "finish", "payload": {"status": "ok", "summary": "all done"}})
    assert "ok" in fin and "all done" in fin


def test_render_subrun_events_name_mode_and_outcome():
    seq = _render_event({"type": "subrun_start", "payload": {
        "n": 1, "label": "step1", "mode": "sequential", "workflow": "general-task"}})
    assert "subtask" in seq and "step1" in seq and "general-task" in seq
    par = _render_event({"type": "subrun_start", "payload": {
        "n": 2, "label": "research", "mode": "parallel", "workflow": "general-task"}})
    assert "subrun" in par and "subtask" not in par     # mode decides the noun
    end = _render_event({"type": "subrun_end", "payload": {
        "n": 2, "label": "research", "mode": "parallel", "status": "ok", "turns": 5}})
    assert "#2" in end and "ok" in end and "5 turns" in end


def test_render_unknown_event_type_is_silent():
    assert _render_event({"type": "no_such_event", "payload": {}}) is None


# ------------------------------------------------------- _parse_model_overrides


def test_parse_model_overrides_accepts_every_role():
    out = _parse_model_overrides(["main=opus", "subroutine=cheap", "tool_call=fast",
                                  "uncensored=raw"])
    assert out == {"main": "opus", "subroutine": "cheap", "tool_call": "fast",
                   "uncensored": "raw"}
    assert _parse_model_overrides([]) == {}
    assert _parse_model_overrides(None) == {}
    # a repeated role: the last value wins (argparse append order)
    assert _parse_model_overrides(["main=a", "main=b"]) == {"main": "b"}


def test_parse_model_overrides_rejects_junk():
    for bad in ("main", "=opus", "main=", "flavor=opus"):
        with pytest.raises(SystemExit):
            _parse_model_overrides([bad])
