"""A cancelled background model call's spend BOOKS — the behaviour, not the wording (F722).

`test_background_actions.py` already asserts that the `handle` field's description SAYS the
spend books. That is a documentation test, and the operator's hand-off named the gap it left:
the behaviour was never checked, and it was wrong. A model call parks its reading only once the
provider answers, so a call cancelled WHILE RUNNING parked nothing at `cancel` time, and the
reading that arrived afterwards sat in a parking slot whose Pending had already left the live
list — which both `collect` and `settle` iterate.

The fake completion here sleeps, then books, in that order: that is the real timing, and a test
whose fake books before sleeping would pass against the broken engine.
"""

from __future__ import annotations

import time

import pytest

from conftest import finish, util
from helpers import server_for, set_capabilities
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260101-000000"

#: Big enough that no other spend in a scripted run could be mistaken for it.
PARKED = {"in": 31337, "out": 4242}


def _server(routine_dir):
    s = server_for(routine_dir)
    set_capabilities(routine_dir, actions=["memory_read", "memory_write"])
    return s


#: The fake provider's latency. Long enough that the NEXT scripted turn (which cancels) is
#: certainly taken while the call is still running — scripted replies return in microseconds —
#: and short enough that a later boundary follows its parking.
PROVIDER_S = 0.3

#: Every turn after the cancel is made this slow, so the boundaries that sweep the ledger land
#: AFTER the provider has answered. Without it the whole run outpaces the fake and nothing has
#: parked by the time `settle` gives up — which is the engine behaving correctly on a run that
#: ended first, and a test that could never see the fix.
TURN_S = 0.5


@pytest.fixture
def slow_booking_llm(monkeypatch):
    """An `llm` that answers slowly and books WHEN IT ANSWERS — the provider's real order.

    A fake that booked before sleeping would park at `cancel` time and pass against the broken
    engine, which is exactly the hole the wording-only assertion left.
    """
    import rsched.engine.executor as executor_mod

    def slow_llm(action, ctx):
        time.sleep(PROVIDER_S)
        ctx.add_usage(dict(PARKED))
        return {"kind": "llm", "text": "an answer nobody reads"}

    def slow_util(action, ctx):
        time.sleep(TURN_S)
        return {"kind": "util", "name": action.get("name"), "exit": 0,
                "stdout": "slow", "stderr": ""}

    monkeypatch.setitem(executor_mod.DISPATCH, "llm", slow_llm)
    monkeypatch.setitem(executor_mod.DISPATCH, "util", slow_util)
    return slow_llm


def _late_bookings(events: list[dict]) -> list[dict]:
    return [e["payload"] for e in events if e["type"] == "observation"
            and e["payload"].get("late_booking")]


@pytest.mark.usefixtures("slow_booking_llm")
def test_a_cancelled_llm_books_the_spend_that_arrives_after_the_cancel(
        make_routine, scripted):
    """THE claim this file exists for. The call is cancelled a turn after it starts, while the
    fake provider is still sleeping; its reading lands during a later turn, and the engine
    books it at the next boundary instead of orphaning it in the parking area.
    """
    d = make_routine(slug="bgbooklate")
    scripted([
        {"say": "Backgrounding a slow llm.", "kind": "llm", "prompt": "think slowly",
         "background": True},
        {"say": "Dropping it.", "kind": "kill", "handle": "bg1"},
        util("list", say="A turn while it finishes unread."),
        util("list", say="And another, by which time it has parked."),
        util("list", say="And a third, so the boundary has certainly run."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    events, _ = read_events(run_dir / "transcript.jsonl")
    late = _late_bookings(events)
    assert late, (
        "the cancelled call's spend was never booked: its reading arrived in a parking slot "
        "whose Pending had already left the live list, which is exactly F722")
    assert late[0]["handle"] == "bg1", late[0]
    assert late[0]["usage_booked"]["in"] == PARKED["in"], late[0]
    assert late[0]["cancelled"] is True, (
        "a late booking must say WHICH loss it belongs to — a reader folding it with an "
        "ordinary collection could not tell a dropped answer from a delivered one")


@pytest.mark.usefixtures("slow_booking_llm")
def test_the_run_total_carries_a_cancelled_calls_tokens(
        make_routine, scripted):
    """The number that matters: the run's own usage, which is what a budget is judged against
    and what the cost surfaces add up. A transcript event nobody folds into `usage` would make
    the record right and the accounting still wrong.
    """
    d = make_routine(slug="bgbooktotal")
    scripted([
        {"say": "Backgrounding a slow llm.", "kind": "llm", "prompt": "think slowly",
         "background": True},
        {"say": "Dropping it.", "kind": "kill", "handle": "bg1"},
        util("list", say="A turn while it finishes unread."),
        util("list", say="And another."),
        util("list", say="And a third."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    import json
    recorded = json.loads((run_dir / "status.json").read_text())
    total = recorded.get("usage") or {}
    assert total.get("in", 0) >= PARKED["in"], (
        f"the run's own usage does not carry the cancelled call's tokens: {total}. "
        "'what it spent still books against your budget' is the promise in the `handle` "
        "field's schema description, in background.py's docstring and in cancel's own "
        "observation note")


@pytest.mark.usefixtures("slow_booking_llm")
def test_a_cancelled_call_books_at_most_once(
        make_routine, scripted):
    """A ledger swept at every boundary must not book the same reading twice: `take_deferred_usage`
    POPS, and the handle leaves the ledger as soon as it pays, so a run with five later
    boundaries still books one reading.
    """
    d = make_routine(slug="bgbookonce")
    scripted([
        {"say": "Backgrounding a slow llm.", "kind": "llm", "prompt": "think slowly",
         "background": True},
        {"say": "Dropping it.", "kind": "kill", "handle": "bg1"},
        util("list", say="One."),
        util("list", say="Two."),
        util("list", say="Three."),
        util("list", say="Four."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    events, _ = read_events(run_dir / "transcript.jsonl")
    late = _late_bookings(events)
    assert len(late) == 1, f"booked {len(late)} times, not once: {late}"

    import json
    total = json.loads((run_dir / "status.json").read_text()).get("usage") or {}
    assert total.get("in", 0) < 2 * PARKED["in"], (
        f"the reading was folded more than once into the run's usage: {total}")


def test_a_cancelled_util_still_needs_no_late_booking(
        make_routine, scripted):
    """The negative case, so the sweep cannot become noise: a `util` spends no model tokens, so
    its cancel owes nothing and no late-booking event is written at all.
    """
    d = make_routine(slug="bgbooknone")
    scripted([
        {"say": "Backgrounding a util.", "kind": "util", "name": "list", "background": True},
        {"say": "Dropping it.", "kind": "kill", "handle": "bg1"},
        util("list", say="A turn after."),
        finish(summary="done" + " ." * 20),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok", status

    events, _ = read_events(run_dir / "transcript.jsonl")
    assert _late_bookings(events) == [], (
        "a cancelled util booked a late reading — the sweep is writing events for calls that "
        "never park anything, which makes every real one harder to see")
