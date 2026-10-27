"""A rung is never dispatched over a ZERO-WIDTH window (R2958/R2960).

Two routines reported the same thing from different angles: an oversight rung started with a
dispatch carrying no evidence — `since_turn: 0`, `turn: 0` — so the rung's run had nothing to
judge and spent a whole run saying so. `engine/ladder.py:261` assembles the dispatch faithfully
from those two numbers, so the bug was upstream: `rung_is_due` returned a PULL-FORWARD reason
before the interval was consulted, and a pull-forward fires regardless of the interval — which
at turn 0 means a window with no turns in it.

The fix is deliberately narrow, because the module's own design sentence is right about the
case it defends: *"an EMPTY interval is still a dispatch — a worker that emitted nothing since
the last rung is itself the signal"*. A STALLED worker (turns elapsed, nothing to show) is
exactly what a rung should see. Zero turns is not that: there are no `says`, no histogram, no
repetition signals and no worker answers, so every verdict the rung could give is uninformed,
while the rung's own brief tells it the engine's counts are the evidence.

So these pin three things together, and the middle one is what keeps the fix from being a
regression of the pull-forward itself:

- a zero-width window does not dispatch, whatever the reason;
- the REASON IS NOT SWALLOWED — `last_rung_turn` is untouched, so the next boundary that has a
  turn in it fires on the same pull-forward. An escalation a person asked for must never be
  lost, only deferred to the first moment there is something to look at;
- a stalled worker (the interval elapsed, nothing emitted) still dispatches, unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.engine import ladder

SETTINGS = {"enabled": True, "turn_interval": 10, "rung_height": 2, "oversight_turns": 20}


class _Loop:
    """The attributes `rung_is_due` reads, and nothing else.

    `escalation_requested` and `outcome_claimed_met` live on the ctx. `repeat_failure_signal` is
    read off the LOOP (`ladder.pull_forward_reason`, via `getattr`) and is deliberately NOT
    declared here: `EngineLoop` does not declare it either, and `tests/test_double_conformance`
    fails a double that assigns an attribute the real class lacks (F644). It is set from outside
    in `_with_repeats` below — exactly as `tests/test_ladder_trigger` sets it — so the double
    stays conformant while the production read stays exercised.
    """

    def __init__(self, *, turn: int, last_rung_turn: int = 0, requested: bool = False,
                 claimed: bool = False, depth: int = 0):
        self.ctx = SimpleNamespace(
            turn=turn, last_rung_turn=last_rung_turn, depth=depth,
            escalation_requested=requested, outcome_claimed_met=claimed,
            budgets=SimpleNamespace(max_subrun_depth=2))


def _with_repeats(loop: _Loop, repeats: list) -> _Loop:
    """A loop carrying the repeat-failure signal, set the way the engine would set it.

    WORTH KNOWING, found by this file's conformance red: no module in `src/` assigns
    `repeat_failure_signal` at all — `ladder.pull_forward_reason` reads it through `getattr`,
    so the `repeat_failure` pull-forward cannot fire in production today. The reason is in the
    vocabulary (`ladder.PULL_FORWARD[0]`) and its predicate is reachable only from a test.
    Reported rather than fixed here: giving it a writer is a behaviour change of its own.
    """
    loop.repeat_failure_signal = repeats
    return loop


def test_the_width_judgement_is_one_pure_predicate():
    """It lives in the ladder's PURE half (`oversight`) because that is what it is — and the
    trigger has one call to it, so there is no second opinion about which windows are vacuous.
    """
    from rsched.engine import oversight

    assert oversight.worth_a_rung(0) is False
    assert oversight.worth_a_rung(1) is True
    assert oversight.worth_a_rung(40) is True


def test_a_person_s_escalation_at_turn_zero_does_not_dispatch_an_empty_window():
    """The reported shape: the run has taken no turns, so there is nothing to judge."""
    assert ladder.rung_is_due(_Loop(turn=0, requested=True), SETTINGS) is None


def test_the_same_escalation_fires_at_the_first_boundary_with_a_turn_in_it():
    """NOT SWALLOWED — the whole point of the narrow fix. `last_rung_turn` is untouched by the
    refusal, so the request survives to the first window that has something in it.
    """
    reason = ladder.rung_is_due(_Loop(turn=1, requested=True), SETTINGS)
    assert reason == ladder.PULL_FORWARD[2]      # "requested"


def test_every_pull_forward_reason_is_refused_at_zero_width_and_fires_at_one_turn():
    """All three, because a reason that keeps its own path is a reason that keeps the defect."""
    for make in (lambda t: _Loop(turn=t, requested=True),
                 lambda t: _Loop(turn=t, claimed=True),
                 lambda t: _with_repeats(_Loop(turn=t), [{"x": 1}])):
        assert ladder.rung_is_due(make(0), SETTINGS) is None
        assert ladder.rung_is_due(make(1), SETTINGS) is not None


def test_a_zero_width_window_after_a_rung_is_refused_too():
    """Not only at turn 0: a rung that just ran leaves `last_rung_turn == turn`, and a
    pull-forward on that same boundary would hand the next rung the same empty window.
    """
    assert ladder.rung_is_due(
        _Loop(turn=40, last_rung_turn=40, requested=True), SETTINGS) is None
    assert ladder.rung_is_due(
        _Loop(turn=41, last_rung_turn=40, requested=True), SETTINGS) is not None


def test_a_stalled_worker_still_dispatches_which_is_the_design_sentence_it_defends():
    """`oversight.build_dispatch`'s rule stands: turns elapsed with nothing to show for them IS
    the signal. A thin window is a dispatch; only a zero-width one is not.
    """
    assert ladder.rung_is_due(_Loop(turn=10, last_rung_turn=0), SETTINGS) == "interval"
    assert ladder.rung_is_due(_with_repeats(_Loop(turn=2, last_rung_turn=0), [{"x": 1}]),
                              SETTINGS) is not None


def test_the_ordinary_interval_and_the_ceiling_are_untouched():
    assert ladder.rung_is_due(_Loop(turn=9, last_rung_turn=0), SETTINGS) is None
    assert ladder.rung_is_due(_Loop(turn=10, last_rung_turn=0), SETTINGS) == "interval"
    # at the ladder's ceiling nothing fires, pull-forward or not — the top rung answers to a
    # person, and that check must stay BEFORE any reason is consulted
    assert ladder.rung_is_due(_Loop(turn=50, requested=True, depth=2), SETTINGS) is None
    assert ladder.rung_is_due(_Loop(turn=50, last_rung_turn=0, depth=2), SETTINGS) is None
