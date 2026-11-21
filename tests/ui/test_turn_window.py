"""The runs table says which WINDOW a resumed run's turn count was budgeted against."""

from __future__ import annotations

import json

from playwright.sync_api import expect


def _resume(ui, slug: str, ts: str, *, turn: int, base_turn: int, max_turns: int,
            turns_left: int) -> None:
    """Rewrite a seeded run's status.json the way the engine writes a RESUMED leg's."""
    path = ui.routine_dir(slug) / "runs" / ts / "status.json"
    st = json.loads(path.read_text(encoding="utf-8"))
    st["turn"] = turn
    st["budgets"] = {"turns_left": turns_left, "total_turns": turn, "base_turn": base_turn,
                     "window_turns": turn - base_turn, "max_turns": max_turns,
                     "max_total_turns": max_turns * 3, "wall_clock_left_s": None, "spent": None}
    path.write_text(json.dumps(st), encoding="utf-8")


def test_a_resumed_runs_turn_count_names_its_window(ui, ui_page):
    """freelance-radar's measured pair, on the page: 348 turns, a window of 200 re-based at
    169 — so 179 of that window are spent and the ceiling plainly did not fail."""
    ui.seed_run("uir", "20260923-043003", "finished", summary="resumed leg")
    _resume(ui, "uir", "20260923-043003", turn=348, base_turn=169, max_turns=200,
            turns_left=21)
    ui_page.goto(f"{ui.url}#/routine/uir")
    cell = ui_page.locator(".runs-box tbody tr").first.locator("td").nth(2)
    expect(cell).to_have_text("348 (window 179/200, resumed at 169)")


def test_a_run_that_never_resumed_still_reads_as_one_number(ui, ui_page):
    """No window, no parenthetical: the overwhelming majority of rows must stay as short as
    they were, or the explanation becomes the noise."""
    ui.seed_run("uir", "20260924-010000", "finished", summary="one leg")
    ui_page.goto(f"{ui.url}#/routine/uir")
    cell = ui_page.locator(".runs-box tbody tr").first.locator("td").nth(2)
    expect(cell).to_have_text("2")
