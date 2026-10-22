"""`max_turns` bounds a resume WINDOW, not a run — and status.json now says so (D155).

THE MEASUREMENT this file exists to pin (audit F564, four runs, no `finish` action between
them — nothing had gone wrong):

    routine              max_turns  status.json turn  budgets.turns_left
    freelance-radar            200               348                 179
    weightloss                 200               276                 175
    llmsectest-weekday         400               423                 338

A reader of `turn 348` beside `179 turns left of 200` can only conclude the ceiling failed.
It did not: `turns_left` is WINDOW-scoped (a resume re-bases the budget at the resume point,
engine/boot.py) while `turn` is cumulative, and nothing in status.json carried the base turn
that reconciles them. Half A of D155 makes the pair legible; half B gives the run-spanning
cap (`max_total_turns`) a real default so an unattended resume chain is bounded at all.
"""

from __future__ import annotations

from rsched.config.base import DEFAULT_BUDGETS
from rsched.engine.budgets_config import Budgets
from rsched.engine.turnwindow import (
    DERIVE,
    TOTAL_TURN_MULTIPLE,
    run_row,
    status_budgets,
    total_turn_cap,
)


class _Row:
    """A registry.RunInfo as the read surfaces see it (only the fields a row needs)."""

    def __init__(self, *, turn: int, base_turn: int, max_turns: int) -> None:
        self.run_id, self.ts, self.state, self.summary = "r:20260923-043003", "20260923-043003", \
            "finished", "ok"
        self.usage, self.elapsed_s = {"in": 1, "out": 2}, 60
        self.turn, self.base_turn, self.max_turns = turn, base_turn, max_turns


def _block(**kw):
    args = {"turn": 348, "base_turn": 327, "max_turns": 200, "max_total_turns": 600,
            "turns_left": 179, "wall_left_min": None, "spent": None}
    args.update(kw)
    return status_budgets(**args)


def test_status_budgets_reconciles_the_freelance_radar_pair():
    """freelance-radar 20260923-043003: turn 348, turns_left 179, max_turns 200. The block
    must now carry WHY — the window was re-based at turn 327, so 21 of its 200 are spent."""
    b = _block()
    assert b["turns_left"] == 179                 # unchanged: the window's own remainder
    assert b["total_turns"] == 348                # the cumulative count status.json's `turn` is
    assert b["base_turn"] == 327                  # what a resume re-based the budget at
    assert b["window_turns"] == 21                # 348 - 327: turns spent in THIS window
    assert b["max_turns"] == 200                  # the window's ceiling, no config lookup needed
    assert b["max_total_turns"] == 600            # the run-spanning cap, beside the window's
    # the pair can no longer disagree: used + left == the window's ceiling
    assert b["window_turns"] + b["turns_left"] == b["max_turns"]


def test_a_run_that_never_resumed_reads_exactly_as_before():
    b = _block(turn=42, base_turn=0, turns_left=158)
    assert b["base_turn"] == 0 and b["window_turns"] == 42 and b["total_turns"] == 42
    # and the row carries no window, so no surface prints a parenthetical for it
    assert run_row(_Row(turn=42, base_turn=0, max_turns=200))["window"] is None


def test_run_row_names_the_window_a_resumed_runs_turn_count_belongs_to():
    row = run_row(_Row(turn=348, base_turn=169, max_turns=200))
    assert row["turn"] == 348                     # the cumulative count the table has always shown
    assert row["window"] == {"base_turn": 169, "turns": 179, "max_turns": 200}


def test_run_row_has_no_window_for_a_run_predating_the_field():
    """An old status.json carries no max_turns in its budget block: the row says nothing
    rather than inventing a window of 0."""
    assert run_row(_Row(turn=12, base_turn=0, max_turns=0))["window"] is None


def test_max_total_turns_defaults_to_three_windows():
    """Half B: the run-spanning cap is DERIVED from max_turns instead of being unlimited, so
    an unattended resume chain cannot run forever."""
    assert DEFAULT_BUDGETS["max_total_turns"] == DERIVE
    assert TOTAL_TURN_MULTIPLE == 3
    b = Budgets.from_config({**DEFAULT_BUDGETS, "max_turns": 200})
    assert b.max_total_turns == 600
    assert total_turn_cap(400, DERIVE) == 1200


def test_an_explicit_total_cap_wins_including_a_deliberate_unlimited():
    assert total_turn_cap(200, 500) == 500
    assert total_turn_cap(200, -1) == -1          # -1 = deliberately unlimited, still honoured
    assert Budgets.from_config({**DEFAULT_BUDGETS, "max_turns": 200,
                                "max_total_turns": -1}).max_total_turns == -1
    assert Budgets.from_config({**DEFAULT_BUDGETS, "max_turns": 200,
                                "max_total_turns": 50}).max_total_turns == 50


def test_an_unlimited_window_derives_no_total_cap():
    """max_turns = -1 lifts the window ceiling; 3 x unlimited is not a number, so the
    run-spanning cap stays unlimited rather than becoming -3."""
    assert total_turn_cap(-1, DERIVE) == -1
    from rsched.conversations import CONVERSATION_BUDGETS

    # a conversation has NO ceilings by default (operator, 2026-10-08) — half B must not
    # quietly give every conversation a 3-reply life
    assert Budgets.from_config(CONVERSATION_BUDGETS).max_total_turns == -1


def test_a_resume_chain_stops_at_the_derived_cap_with_a_fresh_window():
    """The bound half B exists for: the window is fresh (5 turns of 200) yet the run has
    spent its derived 600 across windows, so it stops — and says which cap stopped it."""
    led = Budgets.from_config({**DEFAULT_BUDGETS, "max_turns": 200}).ledger()
    assert led.spent({"turns": 5, "total_turns": 599}) is None
    v = led.spent({"turns": 5, "total_turns": 600})
    assert v is not None and v["resource"] == "total_turns" and v["limit"] == 600


def test_the_total_cap_refusal_says_which_cap_and_that_it_is_run_spanning():
    """A run told only "turn budget exhausted" cannot tell the two caps apart — the same
    illegibility half A fixes. The message names the key AND that it spans resume windows."""
    led = Budgets.from_config({**DEFAULT_BUDGETS, "max_turns": 200}).ledger()
    msg = led.spent({"turns": 5, "total_turns": 600})["message"]
    assert "max_total_turns" in msg
    assert "resume window" in msg                 # it is the run-spanning cap...
    assert "max_turns" in msg                     # ...named against the per-window one
    warn = dict(led.warnings({"turns": 5, "total_turns": 510}))
    assert "max_total_turns" in warn["total_turns@0.85"]
    assert "resume window" in warn["total_turns@0.85"]
