"""D227: a run is told what is LEFT — remaining turns, minutes and (when limited) tokens."""

from __future__ import annotations

from pathlib import Path

from conftest import finish, write_file
from helpers import run_context, server_config, server_for
from rsched.engine.turnwindow import status_budgets

TS = "20260708-070000"
REPO = Path(__file__).resolve().parents[1]


def _block(**kw) -> dict:
    """A status.json budget block — the ONE reading the line is rendered from."""
    args = {"turn": 13, "base_turn": 0, "max_turns": 60, "max_total_turns": 180,
            "turns_left": 47, "wall_left_min": 38.5, "spent": None}
    args.update(kw)
    return status_budgets(**args)


def _line(block: dict, *, wall: int = 45, ceiling: int = -1, tokens: int | None = None) -> str:
    # imported INSIDE each test on purpose: before the renderer exists this file still
    # COLLECTS, so the end-to-end test below fails on behaviour rather than on an import
    from rsched.engine.turnwindow import budget_left

    return budget_left(block, wall_ceiling_min=wall, token_ceiling=ceiling, tokens_left=tokens)


def test_the_line_carries_the_three_remainders_each_beside_its_ceiling():
    """His three numbers, and the ceiling each is a remainder of (constraint 2)."""
    assert _line(_block(), ceiling=500_000, tokens=412_345) == (
        "\n[BUDGET LEFT: 47 of 60 turns · 38 of 45 min · 412,345 of 500,000 tokens]")


def test_an_unlimited_axis_prints_nothing_for_it():
    """"(if limited)", taken literally: an unlimited budget costs the prompt no bytes."""
    assert "tokens" not in _line(_block())                      # the default: unlimited tokens
    assert "min" not in _line(_block(wall_left_min=None))
    assert _line(_block(turns_left=None)) == "\n[BUDGET LEFT: 38 of 45 min]"
    # a run with no ceiling anywhere (a conversation, by default) gets no line at all
    assert _line(_block(turns_left=None, wall_left_min=None)) == ""


def test_a_resumed_leg_reads_as_the_fresh_window_it_was_given():
    """Constraint 2, on F564's own numbers (freelance-radar 20260923-043003): turn 348,
    turns_left 179, max_turns 200. The run is told its WINDOW's remainder beside its window's
    ceiling, and that the window was re-based — never the cumulative 348, which is what read
    as a ceiling that had failed."""
    b = _block(turn=348, base_turn=327, max_turns=200, turns_left=179)
    assert b["window_turns"] + b["turns_left"] == b["max_turns"]      # reconciles on its face
    line = _line(b)
    assert "179 of 200 turns (this resume window, re-based at turn 327)" in line
    assert "348" not in line


def test_the_line_states_a_fact_and_never_tells_the_run_to_pace():
    """Constraint 1: it sits beside a paragraph insisting budgets are never a pace, so it
    carries numbers and no instruction — and that paragraph is still there to sit beside."""
    line = _line(_block(turns_left=2, wall_left_min=1.0), ceiling=500_000, tokens=900).lower()
    for word in ("converge", "hurry", "must", "should", "wind down", "stop", "ration"):
        assert word not in line
    harness = (REPO / "src" / "rsched" / "engine" / "harness.py").read_text(encoding="utf-8")
    assert "never a pace, and never a ration" in harness


def test_the_tail_is_the_same_reading_status_json_records(make_routine, tmp_path):
    """ONE reading, not two that can disagree: the tail and status.json's `budgets` block are
    rendered from the same `budget_block` call."""
    from rsched.engine import turnwindow
    from rsched.paths import read_json

    d = make_routine(slug="oneread", budgets={"max_turns": 60, "max_wall_clock_min": 45})
    ctx = run_context(d, TS, server=server_config(libraries_home=tmp_path / "libraries"))
    ctx.turn = 13
    block = turnwindow.budget_block(ctx)
    assert block["turns_left"] == 47 and block["window_turns"] == 13
    tail = turnwindow.budget_tail(ctx)
    assert f"{block['turns_left']} of 60 turns" in tail
    assert f"{block['wall_clock_left_s'] // 60} of 45 min" in tail
    ctx.write_status()
    recorded = read_json(ctx.run_dir / "status.json")["budgets"]
    assert recorded["turns_left"] == block["turns_left"] == 47
    assert recorded["max_turns"] == 60 and recorded["base_turn"] == 0


def test_every_observation_tells_the_run_what_is_left(make_routine, scripted):
    """The per-turn surface, end to end: the line rides EVERY observation and MOVES. A number
    composed into the harness contract or the state digest would be frozen at turn 1 — this is
    the test that tells those two apart."""
    from rsched.engine.runtime import run_routine

    d = make_routine(slug="leftr", budgets={"max_turns": 12, "max_wall_clock_min": 45})
    ep = scripted([*[write_file(f"state/p{i}.txt", say=f"Step {i}.") for i in range(4)],
                   finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    tails = [m["content"] for m in ep.calls[-1]["messages"]
             if m["role"] == "user" and "[BUDGET LEFT:" in m["content"]]
    assert len(tails) == 4            # one per observation
    turns = [int(t.split("[BUDGET LEFT: ")[1].split(" of ")[0]) for t in tails]
    assert turns == [11, 10, 9, 8]    # it moves with the run; a frozen number would repeat
    # all three axes are limited for this routine, so all three are given — each beside its
    # own ceiling, and the token figure is the live meter rather than a restated ceiling
    assert "11 of 12 turns" in tails[0] and "of 45 min" in tails[0]
    assert "of 100,000 tokens" in tails[0]
    assert int(tails[0].split(" of 100,000")[0].split("· ")[-1].replace(",", "")) < 100_000


def test_an_unlimited_token_budget_costs_the_prompt_nothing_end_to_end(make_routine, scripted):
    """"(if limited)" through the real loop, not just the renderer: the default token budget
    IS unlimited (-1) on this instance, so the commonest run pays for two axes, not three."""
    from rsched.engine.runtime import run_routine

    d = make_routine(slug="unlimr", budgets={"max_turns": 12, "max_wall_clock_min": 45,
                                             "max_total_tokens": -1})
    ep = scripted([write_file("state/p.txt", say="One step."), finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    tail = next(m["content"] for m in ep.calls[-1]["messages"]
                if m["role"] == "user" and "[BUDGET LEFT:" in m["content"])
    assert "[BUDGET LEFT: 11 of 12 turns · 44 of 45 min]" in tail
    assert "token" not in tail and "unlimited" not in tail


def test_the_measured_cost_per_turn_is_what_the_decision_was_about():
    """D227 is a decision about the PROMPT'S SIZE, so the cost is pinned, not estimated."""
    two_axes = _line(_block())                                   # the common run: no token cap
    three_axes = _line(_block(), ceiling=500_000, tokens=412_345)
    resumed = _line(_block(turn=348, base_turn=327, max_turns=200, turns_left=179))
    assert len(two_axes.encode()) == 46                          # 46 B/turn = 13.8 kB over 300
    assert len(three_axes.encode()) == 75                        # 75 B/turn = 22.5 kB over 300
    assert len(resumed.encode()) == 91                            # only a resumed leg pays this
    # ~12-20 tokens a turn. The cheapest honest form his answer allows: dropping the ceilings
    # would save 18 B and break constraint 2 (window_turns + turns_left == max_turns).


def test_the_doc_documents_the_tail_where_tails_are_documented():
    """House rule 3: the doc surface moves in the same change. §3b is where a tail is
    documented, in the order the model reads them."""
    doc = (REPO / "docs" / "prompt-anatomy.md").read_text(encoding="utf-8")
    section = doc[doc.index("### 3b"):doc.index("### 3c")]
    assert "[BUDGET LEFT:" in section
    assert "composed ONCE at run start" in doc or "frozen at turn 1" in section
