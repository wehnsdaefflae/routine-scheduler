"""A live budget raise WITHDRAWS the spent reserved finish turn — D244 (review item E1.4)."""

from __future__ import annotations

from conftest import finish, write_file
from helpers import prompt_text, server_for
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json

TS = "20260907-090000"


def _raise_turns(run_dir, to: int = 12) -> None:
    """The web layer's half of F337: a config PATCH on a LIVE run writes control.json, and the
    engine only ever READS it. `budgets` is a live-classified field (`configflow`)."""
    atomic_write_json(run_dir / "control.json",
                      {"config_change": {"ts": "t-raise", "fields": ["budgets"],
                                         "values": {"budgets": {"max_turns": to}}}})


def _raising(run_dir, action: dict, to: int = 12):
    """A scripted reply that writes the raise AS IT IS ASKED FOR — the only way to land it
    mid-run rather than before the first boundary.
    """
    def reply():
        _raise_turns(run_dir, to)
        return action

    return reply


def test_a_raise_arriving_in_the_reserved_turn_gives_the_run_its_turns_back(
        make_routine, scripted):
    """THE DEFECT, end to end: the run spends its 2-turn budget, is handed the reserved finish
    turn, the operator raises `max_turns` — and the run must go on WORKING, not finish.
    """
    d = make_routine(slug="raised", budgets={"max_turns": 2})
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)

    ep = scripted([write_file("state/a.txt", content="one"),
                   # the raise is written WHILE this turn is being asked for, so it is adopted
                   # at the boundary right after the reserve was granted
                   _raising(run_dir, write_file("state/b.txt", content="two")),
                   write_file("state/c.txt", content="three"),   # only reachable if restored
                   finish(summary="Carried on after the raise.")])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    assert (d / "state" / "c.txt").exists(), \
        "the run never got past the reserved turn — the raise was not adopted in time"
    assert status == "ok"
    # it was told, both times: the reserve, and its withdrawal
    shown = prompt_text(ep)
    assert "LAST turn" in shown
    assert "turn budget" in shown.lower()
    notes = [e for e in events if e["type"] == "user_injection"
             and "withdraw" in str(e["payload"].get("text", "")).lower()]
    assert notes, "the reserve was withdrawn silently — the run was told it was its last turn"


def test_a_raise_that_does_not_clear_the_violation_keeps_the_reserve(make_routine, scripted):
    """The other direction, which must NOT change: a raise too small to clear the violation
    leaves the run in its reserved turn. Otherwise this becomes a way to walk past a budget by
    patching it by one.
    """
    d = make_routine(slug="halfraised", budgets={"max_turns": 2})
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)

    scripted([write_file("state/a.txt", content="one"),
              # the same ceiling it already has: still spent, so the reserve must stand
              _raising(run_dir, write_file("state/b.txt", content="two"), to=2),
              finish(summary="Out of turns; stopping here.")])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "ok"            # the authored finish on the reserved turn
    assert not (d / "state" / "c.txt").exists()


def test_the_reserve_is_withdrawn_only_when_the_run_is_really_inside_its_limits(
        make_routine, scripted):
    """And a run that was never over its budget is untouched by a `budgets` patch — the
    withdrawal must not be a second path that clears a flag nobody set.
    """
    d = make_routine(slug="comfortable", budgets={"max_turns": 10})
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)

    scripted([_raising(run_dir, write_file("state/a.txt", content="one"), to=20),
              finish(summary="Nothing was ever spent.")])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")

    assert status == "ok"
    withdrawn = [e for e in events if e["type"] == "user_injection"
                 and "withdraw" in str(e["payload"].get("text", "")).lower()]
    assert withdrawn == [], "a reserve nobody had was 'withdrawn'"
