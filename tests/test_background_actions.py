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

import threading
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


# ---- F633: the flag goes through the SAME per-kind routing the foreground goes through ---------
#
# `actionroute.dispatch_action` took the background branch BEFORE `_route`, and `background.start`
# called `executor.dispatch` for every kind. Two of the eight backgroundable kinds are owned by
# `_route` rather than by `executor.DISPATCH`, both because they need the D39 call-time secret
# gate: `script` is absent from DISPATCH entirely (so a backgrounded one raised
# `KeyError: 'script'` — reported from a live run, R2255) and `util` is present (so it RAN, with
# the gate skipped). One is a crash and one is silent; the silent one is the serious one, because
# an ergonomics flag relocated a security decision out of the user's reach.

PROBE_SCRIPT = '''# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""probe — a trivial script for the background routing test.

net: none
fs: roots
secrets: (none)
"""
print("probe ran")
'''


def test_the_background_runner_matches_the_foreground_routing_table():
    """The unit statement of the defect: for every backgroundable kind, the callable the thread
    uses must be the callable the synchronous path uses. `script` is the case that proves it —
    `executor.DISPATCH` has no entry for it on purpose, because `_route` puts the secret gate in
    front of it, so a runner that consults DISPATCH alone cannot run it at all."""
    from rsched.engine import background, executor

    assert "script" not in executor.DISPATCH, (
        "DISPATCH gained a `script` entry — that fixes the crash and CEMENTS the gate bypass, "
        "which is exactly why the table omits it; route through actionroute._route instead")
    assert background._runner("script") is executor.do_script
    for kind in BACKGROUNDABLE_KINDS:
        if kind == "script":
            continue
        assert background._runner(kind) is executor.dispatch, kind
        assert kind in executor.DISPATCH, (
            f"{kind} is backgroundable, is not in DISPATCH, and has no runner of its own — "
            "it would raise KeyError the moment a run backgrounds it")


# ---- the concurrency cap (D118 phase 4, decided D166) -----------------------------------------

def test_the_cap_is_a_small_fixed_number():
    """D166 decided "a small fixed cap, e.g. 3". The number is part of the decision, not a
    tuning knob a later edit may drift: a cap of 50 would be the unbounded fan-out the decision
    refused, and a cap of 1 would make the feature pointless."""
    from rsched.engine import background

    assert background.MAX_CONCURRENT == 3


def test_the_cap_is_read_before_the_secret_gate():
    """Order matters, not just presence. The secret gate can file a BLOCKING question; asking
    the user to decide a credential exposure for a call that is about to be refused anyway
    spends his attention on nothing. So the cap must be consulted first."""
    import ast
    import inspect
    import textwrap

    from rsched.engine import actionroute

    # The CALL SITES, not the source text: this module explains the secret gate in a comment
    # block that sits ABOVE the cap's call, so a raw substring index compares the prose and
    # passes or fails for the wrong reason. Walk the AST and read the order of the two calls.
    tree = ast.parse(textwrap.dedent(inspect.getsource(actionroute.dispatch_action)))
    # Sorted by line number, not by walk order: `ast.walk` is breadth-first and gives no
    # guarantee about siblings, so a test that reads its order passes by accident.
    calls = sorted((n.lineno, n.func.attr if isinstance(n.func, ast.Attribute)
                    else getattr(n.func, "id", ""))
                   for n in ast.walk(tree) if isinstance(n, ast.Call))
    order = [name for _line, name in calls
             if name in ("refuse_at_capacity", "_gate_for_background")]
    assert "refuse_at_capacity" in order, "the cap is not wired into dispatch at all"
    assert order.index("refuse_at_capacity") < order.index("_gate_for_background"), (
        "the concurrency cap must be read BEFORE the call-time secret gate, or a call that "
        f"will be refused still costs the user a blocking exposure question: {order}")


def test_the_fourth_concurrent_background_call_is_refused_naming_the_live_handles(
        make_routine, scripted, monkeypatch):
    """The cap, through a real run. Three slow calls are in flight; the fourth is REFUSED on
    its own turn — not started, not queued — and the refusal names every live handle, because
    "the run decides what to drop" is only a real choice if it can see what it is choosing
    between."""
    _slow_util(monkeypatch, seconds=3.0)
    d = make_routine(slug="bgcap")
    scripted([
        {**util("websearch", args=["a"], say="One."), "background": True},
        {**util("websearch", args=["b"], say="Two."), "background": True},
        {**util("websearch", args=["c"], say="Three."), "background": True},
        {**util("websearch", args=["d"], say="Four — over the cap."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = [e for e in events if e["type"] == "observation"]

    started = [e for e in obs if e["payload"].get("started")]
    assert len(started) == 3, (
        f"the cap is 3, so exactly three calls may start; got {len(started)}")
    refusals = [e for e in obs if e["payload"].get("at_capacity")]
    assert len(refusals) == 1, f"the fourth flagged call was not refused: {obs}"

    refusal = refusals[0]["payload"]
    assert refusal.get("started") is not True, (
        "a refusal must not look like a started call — nothing was backgrounded")
    assert refusal["limit"] == 3
    live = {e["payload"]["handle"] for e in started}
    assert set(refusal["in_flight"]) == live, (refusal["in_flight"], live)
    for handle in live:
        assert handle in refusal["reason"], (
            f"the refusal does not name live handle {handle}, so the run cannot tell which of "
            f"its own calls to wait for: {refusal['reason']}")
    assert "REFUSED" in refusal["reason"]


def test_a_capacity_refusal_renders_without_reading_a_result_field(
        make_routine, scripted, monkeypatch):
    """The regression this cost on its first run. An observation for an action the engine did
    NOT execute must carry `rejected` + `reason` — the one shape `observations._not_executed`
    words. Carrying a util-shaped `error` instead fell through to the util renderer, which read
    `obs['name']` and raised `KeyError: 'name'`, killing the turn and every later resume (a
    resume re-renders each stored observation). So the refusal is rendered, not just stored."""
    from rsched.engine.observations import format_observation

    _slow_util(monkeypatch, seconds=2.0)
    d = make_routine(slug="bgcaprender")
    scripted([
        {**util("websearch", args=["a"], say="One."), "background": True},
        {**util("websearch", args=["b"], say="Two."), "background": True},
        {**util("websearch", args=["c"], say="Three."), "background": True},
        {**util("websearch", args=["d"], say="Four."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    refusal = next(e["payload"] for e in events if e["type"] == "observation"
                   and e["payload"].get("at_capacity"))
    assert refusal.get("rejected") is True, (
        "a capacity refusal must use the engine's not-executed shape (`rejected` + `reason`); "
        f"this one would reach a per-kind renderer that reads result fields: {refusal}")
    text = format_observation(refusal)
    assert "REFUSED" in text and "cap" in text, text


def test_a_capacity_refusal_starts_no_work_and_frees_as_calls_land(
        make_routine, scripted, monkeypatch):
    """Two claims a counter alone would miss: the refused call's handler never RAN (a refusal
    that still did the work would be a lie), and capacity is a live measure — once the first
    three land, a flagged call is accepted again rather than the run being capped for life."""
    import rsched.engine.executor as executor_mod

    ran: list[str] = []

    def record(action, _ctx):
        ran.append(str(action.get("args", [None])[0]))
        time.sleep(0.2)
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "ok",
                "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", record)
    d = make_routine(slug="bgcapfree")
    scripted([
        {**util("websearch", args=["a"], say="One."), "background": True},
        {**util("websearch", args=["b"], say="Two."), "background": True},
        {**util("websearch", args=["c"], say="Three."), "background": True},
        {**util("websearch", args=["refused"], say="Four."), "background": True},
        util("list", say="Letting them land."),
        util("list", say="Still letting them land."),
        {**util("websearch", args=["e"], say="A slot has freed."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    assert "refused" not in ran, (
        f"the refused call's handler RAN anyway — the refusal did the work it declined: {ran}")
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = [e for e in events if e["type"] == "observation"]
    assert len([e for e in obs if e["payload"].get("at_capacity")]) == 1, (
        "capacity never freed: a run capped once stayed capped for the rest of its life")
    assert len([e for e in obs if e["payload"].get("started")]) == 4, (
        "the fifth flagged call (after three landed) should have been accepted")


# ---- budget accounting (D118 phase 4, decided D167) -------------------------------------------

def test_the_parking_key_and_the_thread_name_are_one_constant():
    """The two halves of the deferral agree on a STRING, and nothing else binds them: the
    handler that books knows nothing about being backgrounded, so the only thing that can tell
    is the thread's name. If `background.start` ever names its threads differently from what
    `run_context.background_parking_key` reads, every background booking silently goes back to
    racing mid-turn folds — no error, just a budget that is wrong again."""
    from rsched.engine import background
    from rsched.engine.run_context import BACKGROUND_THREAD_PREFIX, background_parking_key

    assert background.THREAD_PREFIX is BACKGROUND_THREAD_PREFIX
    assert background_parking_key() is None, "the main thread must never park its spend"

    seen: list[str | None] = []
    t = threading.Thread(target=lambda: seen.append(background_parking_key()),
                         name=f"{BACKGROUND_THREAD_PREFIX}bg7")
    t.start()
    t.join()
    assert seen == [f"{BACKGROUND_THREAD_PREFIX}bg7"], seen


def test_booking_works_on_a_context_built_without_the_dataclass_initialiser():
    """The regression the release gate caught. A `RunContext` is not always built through its
    dataclass initialiser — callers construct one with `RunContext.__new__` and set only the
    fields they need (a direct dispatch, a unit test of accounting), so NO dataclass default
    exists on it. Reading the lock attribute directly made `add_usage` — which every model call
    reaches — raise `AttributeError` on every such context, which is how one line of parking
    reddened `tests/test_loop.py::test_usage_accounting_cache_keys_and_resume_base`.
    """
    from rsched.engine.run_context import RunContext

    ctx = RunContext.__new__(RunContext)      # no __init__, so no defaults at all
    ctx.usage = {"in": 0, "out": 0}
    ctx.add_usage({"in": 7, "out": 3})
    assert ctx.usage == {"in": 7, "out": 3}, ctx.usage

    # …and the parking half is just as total: a background thread booking onto that same
    # bare context must park rather than raise.
    def park():
        ctx.add_usage({"in": 11, "out": 0})

    t = threading.Thread(target=park, name="background-bg1")
    t.start()
    t.join()
    assert ctx.usage == {"in": 7, "out": 3}, "a background thread's spend folded in directly"
    assert ctx.take_deferred_usage("background-bg1") == {"in": 11}, ctx._deferred_usage


def test_a_background_threads_spend_is_parked_and_booked_once_on_collection(
        make_routine, scripted, monkeypatch):
    """D167: "book tokens on collection". A handler booking from the thread folds into a shared
    dict mid-turn, so the number a turn's budget check reads depends on thread timing. Here a
    backgrounded call spends tokens through the ordinary `ctx.add_usage` path; the meter must
    not move while it runs, and must move by exactly that much once the result is collected."""
    import rsched.engine.executor as executor_mod

    meter_while_running: list[int] = []

    def spender(action, ctx):
        # Exactly what a backgrounded `llm` does: the handler books through add_usage from
        # inside the thread, knowing nothing about being backgrounded.
        ctx.add_usage({"in": 700, "out": 300})
        time.sleep(0.3)
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "spent",
                "stderr": ""}

    def watcher(action, ctx):
        # Each watcher turn takes longer than the backgrounded call, so the run cannot finish
        # before the call lands. Without that, every scripted turn completes inside the
        # sleep, nothing is ever collected, and the spend books in `settle` as `unread` —
        # which is correct behaviour for a run that ends too fast, and says nothing about
        # whether COLLECTION books. (Measured: four scripted turns in under 0.3 s.)
        time.sleep(0.25)
        meter_while_running.append(int(ctx.meter()["tokens"]))
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "watched",
                "stderr": ""}

    def dispatch(action, ctx):
        return (spender if action.get("args") else watcher)(action, ctx)

    monkeypatch.setitem(executor_mod.DISPATCH, "util", dispatch)
    d = make_routine(slug="bgbook")
    scripted([
        {**util("websearch", args=["pay"], say="Backgrounding a spending call."),
         "background": True},
        util("list", say="Looking at the meter while it runs."),
        util("list", say="Looking again, while it still runs."),
        util("list", say="Looking after it has landed."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    # DELTAS, not absolute readings: the run's own turns are model calls and spend tokens too,
    # so the meter is never at zero (measured: the harness alone moves it ~15 per turn). The
    # claim is that the 1000 appears at ONE boundary and not before it.
    assert len(meter_while_running) == 3, meter_while_running
    first, second, third = meter_while_running
    assert second - first < 1000, (
        "the background call's tokens reached the meter MID-FLIGHT — they must be parked until "
        f"collection, or a turn's budget check depends on thread timing: {meter_while_running}")
    assert third - second >= 1000, (
        "the background call's 1000 tokens never booked across the boundary that collected it "
        f"— spend no budget ever sees is worse than spend booked late: {meter_while_running}")

    events, _ = read_events(run_dir / "transcript.jsonl")
    booked = [e["payload"]["usage_booked"] for e in events if e["type"] == "observation"
              and e["payload"].get("usage_booked")]
    assert booked == [{"in": 700, "out": 300}], (
        f"the booked spend must be recorded beside the call that caused it, once: {booked}")


def test_an_abandoned_background_calls_spend_still_books(make_routine, scripted, monkeypatch):
    """The tokens were spent at the provider whether or not this run ever read the answer.
    Charging only the calls that LANDED would make "background it and finish" the cheapest way
    to spend money the budget never sees."""
    import rsched.engine.executor as executor_mod

    def spend_then_hang(action, ctx):
        ctx.add_usage({"in": 500, "out": 100})
        time.sleep(30)          # still running when the run finishes
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "late",
                "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", spend_then_hang)
    d = make_routine(slug="bgabandon")
    scripted([
        {**util("websearch", args=["x"], say="Backgrounding."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    abandoned = [e["payload"] for e in events if e["type"] == "observation"
                 and e["payload"].get("abandoned")]
    assert abandoned, "the still-running call was not recorded as abandoned at all"
    assert abandoned[0].get("usage_booked") == {"in": 500, "out": 100}, (
        "an abandoned background call's already-spent tokens were never booked: "
        f"{abandoned[0]}")


def test_a_background_calls_wall_clock_is_the_runs_own_wall_clock(make_routine, scripted,
                                                                  monkeypatch):
    """D167's second half: "count a background call's wall-clock against the run's wall-clock
    budget, so twenty parallel fetches cannot outrun a 240-minute ceiling". The run's clock is
    REAL elapsed time (`meter()["wall_clock"]` = elapsed_s/60), so a background call cannot buy
    extra clock — it is not bookkeeping but a property, and this pins it: time passing inside a
    background call shows up in the meter the next turn reads."""
    import rsched.engine.executor as executor_mod

    clocks: list[float] = []

    def dispatch(action, ctx):
        if action.get("args"):
            time.sleep(0.6)
        else:
            clocks.append(float(ctx.meter()["wall_clock"]))
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "ok",
                "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", dispatch)
    d = make_routine(slug="bgclock")
    scripted([
        util("list", say="Clock at the start."),
        {**util("websearch", args=["slow"], say="Backgrounding a slow call."),
         "background": True},
        util("list", say="Clock after it landed."),
        finish(summary="done" + " ." * 20),
    ])
    status, _run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    assert len(clocks) == 2, clocks
    assert clocks[1] > clocks[0], (
        "wall clock did not advance across a background call — the run's clock must be real "
        f"elapsed time, or parallel fetches outrun the ceiling: {clocks}")


def test_every_add_usage_in_the_engine_goes_through_the_one_booking_point():
    """The structural guard, and the reason this fix is one edit rather than six. A backgrounded
    `llm` reaches `add_usage` at five sites (two in llmaction, three in refusal) and a `decide`
    at one; a per-handler deferral would be six edits that the NEXT handler to book forgets. So
    `RunContext.add_usage` is the single place that decides parked-or-booked, and nothing may
    fold into `ctx.usage` around it."""
    import pathlib

    import rsched.engine.run_context as rc_mod

    # What is forbidden is folding into the RUN's usage from outside `add_usage` — not folding
    # at all: `endpoints/completion.py` and `engine/history.py` legitimately fold into their
    # OWN local totals (a per-completion sum, a replayed-leg total), which no budget reads and
    # no background thread touches. An earlier version of this guard flagged those three lines
    # and said nothing true; the claim is about the destination, so it names the destination.
    engine = pathlib.Path(rc_mod.__file__).parent
    offenders = []
    for path in sorted([*engine.glob("*.py"), *engine.parent.glob("endpoints/*.py")]):
        if path.name == "run_context.py":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "fold_usage(" in line and (".usage" in line or "ctx.usage" in line):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, (
        "something folds into a run's usage outside RunContext.add_usage — background spend "
        f"would bypass the parking that D167 requires: {offenders}")
    # And the positive half: the one booking point really does consult the parking key, so a
    # future edit cannot keep the name and drop the deferral.
    import inspect

    src = inspect.getsource(rc_mod.RunContext.add_usage)
    assert "background_parking_key" in src, (
        "RunContext.add_usage no longer asks whether it is on a background thread — every "
        "backgrounded call's spend is folding mid-turn again (D167)")


def test_every_kind_route_owns_is_gated_before_it_may_be_backgrounded():
    """The structural guard. `_route` owns a branch for a kind only where something must happen
    BEFORE the executor sees it; for `util` and `script` that something is the D39 call-time
    secret gate, which a background thread cannot run (it files a BLOCKING ask and a thread has
    no turn to block on). So every backgroundable kind that `_route` gates must also appear in
    `_BACKGROUND_GATES` — otherwise the flag is a way around the gate."""
    from rsched.engine import actionroute

    assert set(actionroute._BACKGROUND_GATES) == {"util", "script"}
    for kind, gate in actionroute._BACKGROUND_GATES.items():
        assert kind in BACKGROUNDABLE_KINDS, kind
        assert gate is not None


def test_a_backgrounded_script_actually_runs(make_routine, scripted, monkeypatch):
    """The live crash, through a real run: `script` + `background: true` used to raise
    `KeyError: 'script'` inside the thread, which the run read as an engine error instead of its
    result. It must now land the script's real output."""
    d = make_routine(slug="bgsc")
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "probe.py").write_text(PROBE_SCRIPT, encoding="utf-8")
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda rd, name, args, **kw: (0, "probe ran", ""))
    scripted([
        {"say": "Backgrounding my own helper.", "kind": "script", "name": "probe",
         "background": True},
        util("list", say="Working while it runs."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = [e for e in events if e["type"] == "observation"]
    started = [e for e in obs if e["payload"].get("started")]
    assert len(started) == 1 and started[0]["payload"]["kind"] == "script", obs
    handle = started[0]["payload"]["handle"]
    landed = [e for e in obs if e["payload"].get("handle") == handle
              and not e["payload"].get("started")]
    assert len(landed) == 1, "the backgrounded script's result never landed"
    assert landed[0]["payload"].get("engine_error") is not True, (
        f"the backgrounded script raised instead of running: {landed[0]['payload']}")
    assert landed[0]["payload"]["stdout"] == "probe ran"
    assert landed[0]["payload"]["exit"] == 0


def test_a_denied_secret_refuses_a_backgrounded_util_on_the_starting_turn(
        make_routine, scripted, monkeypatch):
    """The silent half. The D39 gate is the user's answer to "may this call see this
    credential"; backgrounding the call used to skip it entirely. The refusal must arrive on the
    turn that asked — not as a deferred observation, and not at all as a completed call."""
    import yaml

    import rsched.engine.executor as executor_mod

    ran: list[str] = []

    def record(action, _ctx):
        ran.append(str(action.get("name")))
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "ran",
                "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "util", record)
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"BG_TOKEN": "v-1"})
    monkeypatch.setattr("rsched.utils_lib.exists", lambda _home, _name: True)
    monkeypatch.setattr(
        "rsched.utils_run.util_needs",
        lambda _home, _name: type("N", (), {"secrets": {"BG_TOKEN"}, "optional": set()})())

    d = make_routine(slug="bgdeny")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["grants"] = {"secret:BG_TOKEN": False}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    scripted([
        {**util("mailer", say="Backgrounding a credential-using call."), "background": True},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    assert ran == [], (
        "the backgrounded util RAN with a secret the user had declined — the flag walked "
        f"around the D39 call-time gate: {ran}")
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = [e for e in events if e["type"] == "observation"]
    assert not [e for e in obs if e["payload"].get("started")], (
        "nothing may be backgrounded once the gate refuses the call")
    refusal = next(e for e in obs if e["payload"].get("declined_secrets"))
    assert refusal["turn"] == 1, "the refusal must reach the turn that asked for the call"
    assert "declined" in refusal["payload"]["reason"]


# ---- cancellation (D118 phase 4, decided D176 option (a)) --------------------------------------

def test_kill_declares_the_handle_field_in_the_flat_action_schema():
    """D176 option (a) is "a NEW `handle` field on `kill`", and a sub-field the flat
    ACTION_SCHEMA does not declare is stripped in SILENCE (`additionalProperties: False`) —
    the way two earlier action fields shipped doing nothing. So the declaration is the first
    claim, before any behaviour."""
    assert "handle" in ACTION_SCHEMA["properties"], (
        "`kill` cannot address a background call: the field is undeclared, so normalisation "
        "strips it and every handle-addressed kill reaches the route as a bare kill")
    assert ACTION_SCHEMA["properties"]["handle"]["type"] == "string"
    assert "handle" in KIND_FIELDS["kill"][1]


def test_the_handle_fields_own_description_states_the_delivery_not_work_ceiling():
    """The operator's hand-off demanded this in these terms: "state the delivery-not-work
    limitation in the field's own schema description so a caller cannot read it as a
    cancellation". The field description IS the only documentation a run gets at the moment it
    decides to cancel — a ceiling recorded only in docs/ is a ceiling the caller never meets.
    """
    text = ACTION_SCHEMA["properties"]["handle"]["description"].lower()
    assert "not the work" in text or "never" in text, text
    assert "spen" in text or "book" in text, (
        f"the description does not say the spend still books, so a run could read "
        f"cancellation as a refund: {text}")
    assert "bg1" in text, "the description does not show the handle's shape"


def test_a_kill_addresses_exactly_one_namespace():
    """Two namespaces stay two namespaces (D176's whole point), so the pair is checked
    semantically: `n` alone and `handle` alone are valid, neither and both are refused with a
    message naming what to drop. `n` could not be REQUIRED any more, which is exactly why this
    check has to exist — without it a handle-less, n-less kill would reach
    `loop.subruns.kill(action["n"])` and raise KeyError."""
    assert validate_action(normalize_action({"say": "x", "kind": "kill", "n": 1})) == []
    assert validate_action(normalize_action(
        {"say": "x", "kind": "kill", "handle": "bg1"})) == []

    both = validate_action(normalize_action(
        {"say": "x", "kind": "kill", "n": 1, "handle": "bg1"}))
    assert any("never both" in p for p in both), both

    neither = validate_action(normalize_action({"say": "x", "kind": "kill"}))
    assert any("'handle'" in p and "'n'" in p for p in neither), neither


def test_a_cancellation_renders_without_reading_the_sub_workflow_number():
    """The `kill` renderer reads `obs['n']` on every branch below the error one, and a
    background cancellation carries a string handle and NO `n`. Rendering it there raises
    `KeyError: 'n'`, which kills the turn AND every later resume (a resume re-renders each
    stored observation) — the exact failure the capacity refusal paid for on its first run."""
    from rsched.engine.observations import format_observation

    text = format_observation({
        "kind": "kill", "handle": "bg2", "cancelled": True, "background_kind": "util",
        "brief": "websearch", "had_landed": False, "in_flight": [],
        "note": "`bg2` will NOT deliver its observation and its slot is free."})
    assert "bg2" in text, text
    assert "kill" in text.lower(), text


def test_cancelling_a_handle_that_is_not_in_flight_teaches_the_valid_ones(
        make_routine, scripted, monkeypatch):
    """An addressing mistake is correctable, so it is an `error` observation naming what IS in
    flight — the shape `subruns.kill` gives an unknown sub-workflow number. The common case is a
    run holding a handle whose call already LANDED: it came from a started observation and still
    looks live, so the message names that reading first."""
    _slow_util(monkeypatch, seconds=2.0)
    d = make_routine(slug="bgcancelmiss")
    scripted([
        {**util("websearch", args=["a"], say="Backgrounding one."), "background": True},
        {"say": "Cancelling a handle I never started.", "kind": "kill", "handle": "bg9"},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    err = next(e["payload"] for e in events if e["type"] == "observation"
               and e["payload"].get("kind") == "kill" and e["payload"].get("error"))
    assert "bg9" in err["error"], err
    assert "bg1" in err["error"], (
        f"the error does not name the handle that IS in flight, so the run cannot correct "
        f"its own addressing: {err['error']}")


def test_a_cancelled_call_delivers_no_observation_and_frees_its_slot(
        make_routine, scripted, monkeypatch):
    """The feature, through a real run, in the three terms the decision allows it to claim.

    Three slow calls fill the cap; one is cancelled; a fourth flagged call is then ACCEPTED
    where it would otherwise have been refused — that acceptance is the slot-freeing proof, and
    it is a stronger claim than reading a counter. The cancelled handle must never appear as a
    landed (non-`started`) observation, and no BACKGROUND RESULT message may carry it.
    """
    _slow_util(monkeypatch, seconds=3.0)
    d = make_routine(slug="bgcancel")
    scripted([
        {**util("websearch", args=["a"], say="One."), "background": True},
        {**util("websearch", args=["b"], say="Two."), "background": True},
        {**util("websearch", args=["c"], say="Three."), "background": True},
        {"say": "I no longer need the second result.", "kind": "kill", "handle": "bg2"},
        {**util("websearch", args=["d"], say="The freed slot takes this."),
         "background": True},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = [e for e in events if e["type"] == "observation"]

    cancels = [e["payload"] for e in obs
               if e["payload"].get("kind") == "kill" and e["payload"].get("cancelled")]
    assert len(cancels) == 1, f"the cancellation did not happen: {obs}"
    assert cancels[0]["handle"] == "bg2"

    assert not [e for e in obs if e["payload"].get("at_capacity")], (
        "the fourth flagged call was refused, so cancelling bg2 did not free its cap slot")
    assert len([e for e in obs if e["payload"].get("started")]) == 4, (
        "four calls should have STARTED: three, then the one the freed slot admitted")

    delivered = [e["payload"] for e in obs
                 if e["payload"].get("background") and e["payload"].get("handle") == "bg2"
                 and not e["payload"].get("started")]
    assert all(p.get("cancelled") for p in delivered), (
        f"a cancelled call delivered its observation anyway: {delivered}")


def test_a_cancelled_call_is_recorded_as_its_own_loss_not_as_an_abandonment(
        make_routine, scripted, monkeypatch):
    """Two losses with different causes must not fold into one key. `abandoned` means the run
    ran out of turns; `cancelled` means the run CHOSE to drop the result. A surface that could
    not tell them apart would read every deliberate drop as a run that overran — and the record
    is the only place anyone can ever see which it was."""
    _slow_util(monkeypatch, seconds=3.0)
    d = make_routine(slug="bgcancelrec")
    scripted([
        {**util("websearch", args=["a"], say="One."), "background": True},
        {"say": "Dropping it.", "kind": "kill", "handle": "bg1"},
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    # `background: true` is what separates the CALL's record from the kill ACTION's own
    # observation: both name the handle, and only the call's record carries the background
    # framing, its `started_turn` and its booked spend — the shape `collect` and `settle`
    # write, so one reader folds all three fates of a handle.
    recorded = [e["payload"] for e in events if e["type"] == "observation"
                and e["payload"].get("handle") == "bg1"
                and e["payload"].get("background")
                and not e["payload"].get("started")]
    assert len(recorded) == 1, (
        f"the cancelled call left no record, so nobody reading the run afterwards can tell "
        f"the work happened and the answer was dropped: {recorded}")
    assert recorded[0].get("cancelled") is True
    assert recorded[0].get("abandoned") is not True, (
        "a cancellation recorded as an abandonment loses the one thing the record is for: "
        "whether the run chose this or ran out of turns")
    assert recorded[0].get("started_turn") == 1


def test_cancelling_frees_the_slot_without_settle_having_to_mop_up(
        make_routine, scripted, monkeypatch):
    """A cancelled call must leave the live list AT ONCE, not at run end: `collect` reads that
    list at every boundary, so a Pending still in it would deliver its result the moment the
    thread finished — the one thing cancellation promises will not happen. The cancelled handle
    therefore appears in NO summary line about what the run never read."""
    _slow_util(monkeypatch, seconds=0.3)
    d = make_routine(slug="bgcancelsettle")
    scripted([
        {**util("websearch", args=["a"], say="One."), "background": True},
        {"say": "Dropping it before it lands.", "kind": "kill", "handle": "bg1"},
        util("list", say="A turn that gives the thread time to finish."),
        util("list", say="And another."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status
    events, _ = read_events(run_dir / "transcript.jsonl")
    landed = [e["payload"] for e in events if e["type"] == "observation"
              and e["payload"].get("handle") == "bg1"
              and not e["payload"].get("started")
              and not e["payload"].get("cancelled")]
    assert landed == [], (
        f"the cancelled call's result was delivered or recorded as unread after all — it was "
        f"still in the live list when its thread finished: {landed}")


# ---- cancellation stops the WORK where it can (the operator's 0.404.0 correction) -------------

def test_the_stoppable_kinds_are_exactly_the_subprocess_ones():
    """The asymmetry has one source of truth, and it is not a guess: `util`, `script` and `shell`
    are the kinds `utils_run.run_jailed` runs, and it is the only place in the engine that polls a
    cancel check inside a wait and ends a process group. Every other backgroundable kind runs
    inside the interpreter, where Python offers no interruption point."""
    from rsched.engine.background import STOPPABLE_KINDS

    assert set(STOPPABLE_KINDS) == {"util", "script", "shell"}
    assert set(STOPPABLE_KINDS) <= set(BACKGROUNDABLE_KINDS), (
        "a kind that cannot be backgrounded cannot be cancelled either")
    for kind in ("llm", "decide", "read_file", "view_image", "memory_read", "read_rule"):
        assert kind not in STOPPABLE_KINDS, (
            f"{kind} runs in the interpreter — claiming its work can be stopped would be the "
            "same overclaim in the other direction")


def test_the_handle_field_states_both_promises_not_one_ceiling():
    """0.404.0's description said "it stops the DELIVERY, not the work" for every kind. The
    operator read that and said: *that sounds like a detach, not like a kill* — correctly, because
    for the three kinds worth cancelling (a ten-minute test run reaches the host through `shell` or
    `script`, never through `llm`) the engine CAN stop the work. The field is the only
    documentation a run meets at the moment it decides, so it carries both cases."""
    text = ACTION_SCHEMA["properties"]["handle"]["description"]
    low = text.lower()
    assert "terminated" in low or "process group" in low, (
        f"the field does not say the work can actually stop: {text}")
    assert "shell" in low and "script" in low, "it must name which kinds stop"
    assert "interpreter" in low or "interrupt" in low, (
        "and which kinds cannot, or the claim flips from understated to overstated")
    assert "spen" in low or "book" in low, "the spend still books, both ways"


def test_cancelling_a_backgrounded_shell_actually_kills_the_subprocess(
        make_routine, scripted, monkeypatch):
    """THE claim. A backgrounded `shell` sleeping far past the run's end is cancelled, and the
    process it started is GONE — not merely undelivered.

    The command writes its own pid, then sleeps 120 s and only then writes a "finished" marker.
    After the cancel the pid must be dead and the marker must never appear: a run that merely
    stopped listening would leave both. This is the difference between a kill and a detach.
    """
    import os
    import signal as sig

    # The turns must be SLOWER than the subprocess takes to SPAWN, or the run ends before bash
    # has written its pid and the test proves nothing about stopping (measured: a 0.46 s run).
    _slow_util(monkeypatch, seconds=0.8)
    d = make_routine(slug="bgkillshell")
    pidfile = d / "state" / "bg.pid"
    donefile = d / "state" / "bg.done"
    cmd = (f"echo $$ > {pidfile}; sleep 120; echo finished > {donefile}")

    scripted([
        {"say": "Backgrounding a long shell command.", "kind": "shell",
         "command": cmd, "timeout_s": 300, "background": True},
        util("list", say="Letting it start."),
        util("list", say="Still letting it start."),
        {"say": "I no longer need it — stop it.", "kind": "kill", "handle": "bg1"},
        util("list", say="Giving the group a moment to die."),
        util("list", say="And another."),
        finish(summary="done" + " ." * 20),
    ])
    server = _server(d)
    set_capabilities(d, actions=["shell", "memory_read", "memory_write"])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok", status

    events, _ = read_events(run_dir / "transcript.jsonl")
    cancels = [e["payload"] for e in events if e["type"] == "observation"
               and e["payload"].get("kind") == "kill" and e["payload"].get("cancelled")]
    assert len(cancels) == 1, f"the cancellation did not happen: {cancels}"
    assert cancels[0]["stopped_work"] is True, (
        "a backgrounded shell is a subprocess — its cancellation must claim, and be, a real stop")

    assert pidfile.exists(), "the command never started, so this test proves nothing"
    pid = int(pidfile.read_text().strip())
    # The group is SIGTERMed inside run_jailed's wait; give the kernel a moment either way.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, sig.SIGKILL)       # never leave a 120 s sleeper behind
        except ProcessLookupError:
            pass
        pytest.fail(f"pid {pid} is STILL ALIVE after the cancel — the work was not stopped, "
                    "which is exactly the detach-wearing-a-kill's-name the operator named")
    assert not donefile.exists(), (
        "the command ran to completion anyway — it was undelivered, not stopped")


def test_cancelling_an_llm_stops_only_the_delivery_and_says_so(
        make_routine, scripted, monkeypatch):
    """The other half, and it must NOT overclaim. An `llm` runs inside the interpreter, so the
    cancel frees the slot and drops the result while the call finishes unread — and the
    observation says that, rather than claiming a stop it did not perform."""
    import rsched.engine.executor as executor_mod

    ran: list[str] = []

    def slow_llm(action, ctx):
        time.sleep(1.5)
        ran.append(str(action.get("prompt")))
        return {"kind": "llm", "text": "an answer nobody reads"}

    monkeypatch.setitem(executor_mod.DISPATCH, "llm", slow_llm)
    d = make_routine(slug="bgkillllm")
    scripted([
        {"say": "Backgrounding a slow llm.", "kind": "llm", "prompt": "think slowly",
         "background": True},
        {"say": "Dropping it.", "kind": "kill", "handle": "bg1"},
        util("list", say="A turn while it finishes unread."),
        util("list", say="And another."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    events, _ = read_events(run_dir / "transcript.jsonl")
    cancel = next(e["payload"] for e in events if e["type"] == "observation"
                  and e["payload"].get("kind") == "kill" and e["payload"].get("cancelled"))
    assert cancel["stopped_work"] is False, (
        "an llm's work cannot be stopped — claiming it was would be the same overclaim as "
        "0.404.0's, pointing the other way")
    # …and the result still never reached the run
    delivered = [e for e in events if e["type"] == "observation"
                 and e["payload"].get("handle") == "bg1"
                 and e["payload"].get("background")
                 and not e["payload"].get("started")
                 and not e["payload"].get("cancelled")]
    assert delivered == [], f"a cancelled llm delivered its observation anyway: {delivered}"


def test_the_cancel_check_is_keyed_by_handle_not_by_the_turn_that_started_it():
    """Why this was unreachable until now, pinned so it cannot regress.

    `control.cancelled_for_turn` keys the F586 intent channel by TURN — deliberately: a cancel
    clicked just as a long call finishes must not kill the NEXT call. A background call outlives
    its starting turn, so that key can never fire for it again. The fix is a context carrying
    `background_stop`, which the same function prefers when present.
    """
    import threading as th

    from rsched.engine.control import cancelled_for_turn

    stop = th.Event()

    class Ctx:
        background_stop = staticmethod(stop.is_set)
        turn = 7
        root_run_dir = None

    check = cancelled_for_turn(Ctx())
    assert check() is False
    stop.set()
    assert check() is True, (
        "the per-handle stop is not consulted, so a background subprocess can never be reached")

    class TurnOnlyCtx:
        turn = 7
        root_run_dir = None

    # no run dir and no handle: never cancelled, the way `_never_aborted` answers for the abort
    assert cancelled_for_turn(TurnOnlyCtx())() is False
