"""D118 phase 1 — a read or a fetch marked `background: true`.

Two halves, deliberately in one file because they are one contract:

* **the field** — which kinds may carry it, that a kind which may NOT is refused with the reason
  rather than having the flag quietly dropped, and that the field survives the schema
  normalisation at all (a sub-field the flat `ACTION_SCHEMA` does not declare is stripped in
  silence, which is how two earlier action fields shipped doing nothing);
* **the behaviour** — through a REAL scripted run with a REAL `Transcript`: the flagged action
  returns a *started* observation on its own turn, and the real observation is appended at a
  LATER turn boundary. A fake transcript that accepts anything cannot fail the way production
  does, so these go through `run_routine`.
"""

import time

import pytest

from conftest import finish, util
from helpers import server_for, set_capabilities
from rsched.engine.actions import (
    BACKGROUNDABLE_KINDS,
    KIND_FIELDS,
    normalize_action,
    validate_action,
)
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-090000"


# ---- the field ---------------------------------------------------------------------------------

def test_background_is_declared_in_the_flat_action_schema():
    """The engine validates and NORMALISES against ACTION_SCHEMA, which is
    `additionalProperties: False` — an undeclared field is stripped with no error and no log
    line, so the feature would silently never run."""
    assert "background" in ACTION_SCHEMA["properties"]
    assert ACTION_SCHEMA["properties"]["background"]["type"] == "boolean"


@pytest.mark.parametrize("kind", BACKGROUNDABLE_KINDS)
def test_every_backgroundable_kind_accepts_the_flag(kind):
    assert "background" in KIND_FIELDS[kind][1], (
        f"{kind} is in BACKGROUNDABLE_KINDS but KIND_FIELDS does not allow the field, so "
        "validate_action would reject every use of it")


@pytest.mark.parametrize("kind", [k for k in KINDS if k not in BACKGROUNDABLE_KINDS])
def test_a_kind_that_may_not_defer_is_refused_with_the_reason(kind):
    """Not merely rejected — rejected with a message that teaches the rule. And crucially NOT
    silently stripped: the flag has to survive normalisation far enough for the validator to
    see it, or the action runs SYNCHRONOUSLY while the model believes it was deferred."""
    required, _ = KIND_FIELDS[kind]
    action = {"say": "x", "kind": kind, "background": True}
    for field in required:                      # a complete action: the strip path's own case
        action[field] = 1 if field == "n" else "x"
    if kind == "finish":
        action["summary"] = "a summary long enough to pass the finish gate's length floor"
    normalized = normalize_action(action)
    assert normalized.get("background") is True, (
        "the flag was dropped before validation — the silent reinterpretation this field "
        "must never have")
    problems = validate_action(normalized)
    assert any("cannot run in the background" in p for p in problems), problems
    assert any("only reads and fetches may be deferred" in p for p in problems), problems


@pytest.mark.parametrize("kind", [k for k in KINDS if k not in BACKGROUNDABLE_KINDS])
def test_the_refusal_is_said_once(kind):
    """The generic stray-field complaint would say it a second time, in a list of thirty
    allowed names that teaches nothing. One precise problem per problem."""
    required, _ = KIND_FIELDS[kind]
    action = {"say": "x", "kind": kind, "background": True}
    for field in required:
        action[field] = 1 if field == "n" else "x"
    problems = validate_action(normalize_action(action))
    assert not any("do not belong to kind=" in p for p in problems), problems


def test_a_mutation_is_not_backgroundable():
    """The safety matrix's whole point: a deferred write could be read stale by the next
    synchronous action. Phase 3's job, not phase 1's."""
    for kind in ("write_file", "edit_file", "write_util", "memory_write", "delete", "move"):
        assert kind not in BACKGROUNDABLE_KINDS


def test_the_control_kinds_are_not_backgroundable():
    for kind in ("finish", "ask_user", "report", "spawn", "subtask", "wait", "kill"):
        assert kind not in BACKGROUNDABLE_KINDS


def test_an_unflagged_action_is_unaffected():
    assert validate_action(normalize_action(
        {"say": "x", "kind": "write_file", "path": "p", "content": "c"})) == []
    assert validate_action(normalize_action({"say": "x", "kind": "util", "name": "websearch"})) == []


# ---- the behaviour, through a real run ---------------------------------------------------------

def _server(routine_dir):
    s = server_for(routine_dir)
    set_capabilities(routine_dir, actions=["memory_read", "memory_write"])
    return s


def _slow_util(monkeypatch, seconds=0.4):
    """Make `util` dispatch take real wall-clock, so "returned at once" is a measurable claim
    rather than a hopeful one."""
    import rsched.engine.executor as executor_mod

    def slow(action, ctx):
        time.sleep(seconds)
        return {"kind": "util", "name": action.get("name"), "exit": 0,
                "stdout": f"slow result for {action.get('name')}", "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", slow)


def test_a_flagged_action_returns_at_once_and_its_result_lands_later(
        make_routine, scripted, monkeypatch):
    """The whole feature in one run: turn 1 backgrounds a slow util and comes straight back,
    turn 2 is taken while it runs, and the REAL observation is appended at a turn boundary
    before the finish."""
    _slow_util(monkeypatch, seconds=0.5)
    d = make_routine(slug="bgr")
    scripted([
        {**util("websearch", args=["q"], say="Backgrounding a slow fetch."), "background": True},
        util("list", say="Working while it runs."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    assert status == "ok", status
    obs = [e for e in events if e["type"] == "observation"]
    started = [e for e in obs if e["payload"].get("started")]
    assert len(started) == 1, obs
    handle = started[0]["payload"]["handle"]
    assert started[0]["payload"]["background"] is True
    assert started[0]["turn"] == 1

    landed = [e for e in obs if e["payload"].get("handle") == handle
              and not e["payload"].get("started")]
    assert len(landed) == 1, (
        "the real observation never landed — a background result that vanishes is the "
        "failure-visibility rule broken by design")
    assert landed[0]["payload"]["started_turn"] == 1
    assert landed[0]["turn"] > started[0]["turn"], (
        "the real observation must be stamped with the turn that READS it, not the turn that "
        "started the call — a boundary runs before the turn it opens is counted, so the naive "
        "stamp records the result as arriving on the turn that started it")
    assert "slow result for websearch" in str(landed[0]["payload"])


def test_the_run_is_told_in_words_where_the_result_will_arrive(
        make_routine, scripted, monkeypatch):
    """The started observation is the only thing the model sees that turn; if it does not say
    the result is coming and that re-running is wrong, the model re-runs the call."""
    _slow_util(monkeypatch, seconds=0.1)
    d = make_routine(slug="bgw")
    scripted([
        {**util("websearch", say="Backgrounding."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    _status, run_dir = run_routine(d, _server(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    started = next(e for e in events
                   if e["type"] == "observation" and e["payload"].get("started"))
    assert started["payload"]["kind"] == "util"
    assert started["payload"]["handle"].startswith("bg")


def test_a_background_failure_is_delivered_not_swallowed(make_routine, scripted, monkeypatch):
    """A handler that RAISES in the background becomes a readable observation, exactly as the
    synchronous path's try/except makes one."""
    import rsched.engine.executor as executor_mod

    def boom(_action, _ctx):
        raise RuntimeError("the fetch exploded")

    monkeypatch.setitem(executor_mod.DISPATCH, "util", boom)
    d = make_routine(slug="bgf")
    scripted([
        {**util("websearch", say="Backgrounding a doomed fetch."), "background": True},
        util("list", say="Carrying on."),
        finish(summary="done" + " ." * 20),
    ])
    _status, run_dir = run_routine(d, _server(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    landed = [e for e in events if e["type"] == "observation"
              and e["payload"].get("background") and not e["payload"].get("started")]
    assert landed, "the raising background call reported nothing at all"
    assert "the fetch exploded" in str(landed[0]["payload"])
    assert landed[0]["payload"].get("engine_error") is True


def test_two_background_calls_both_land(make_routine, scripted, monkeypatch):
    _slow_util(monkeypatch, seconds=0.2)
    d = make_routine(slug="bg2")
    scripted([
        {**util("websearch", args=["a"], say="First."), "background": True},
        {**util("websearch", args=["b"], say="Second."), "background": True},
        util("list", say="Meanwhile."),
        util("list", say="Still working."),
        finish(summary="done" + " ." * 20),
    ])
    _status, run_dir = run_routine(d, _server(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    handles = {e["payload"]["handle"] for e in events if e["type"] == "observation"
               and e["payload"].get("started")}
    landed = {e["payload"]["handle"] for e in events if e["type"] == "observation"
              and e["payload"].get("background") and not e["payload"].get("started")}
    assert len(handles) == 2, handles
    assert handles == landed, (handles, landed)


def test_a_call_still_running_at_finish_is_named_in_the_summary(
        make_routine, scripted, monkeypatch):
    """A run that ends on a call it never read has LOST that work. The transcript records it
    and the summary says it, because a reader of the result is the one person who can tell
    whether it mattered."""
    _slow_util(monkeypatch, seconds=8)
    d = make_routine(slug="bgs")
    scripted([
        {**util("websearch", say="Backgrounding something slow."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    _status, run_dir = run_routine(d, _server(d), run_ts=TS)
    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert "still running at run end" in result, result[-600:]
    events, _ = read_events(run_dir / "transcript.jsonl")
    abandoned = [e for e in events if e["type"] == "observation"
                 and e["payload"].get("abandoned")]
    assert abandoned, "an abandoned background call left no record"
