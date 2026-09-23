"""The Routines table's own two controls: the sortable headers (F208) and the state filter.

Both were uncovered, and the sort direction is the fragile half: re-clicking the ACTIVE column
must REVERSE it rather than be a no-op, which is a rule about the page's stored sort state, not
about the renderer. That state and the renderer now live in different modules (dashboard.js
owns `sortKey`/`sortDir` and persists them; dashboard-rows.js only draws the arrow and reports
the click), so the wiring between them is exactly what can break silently — the header would
still render, and still be clickable, and simply stop reversing.
"""

from __future__ import annotations

from playwright.sync_api import expect


def _seed_last_run(ui):
    """A finished run, so the fixture routine has a last-run state to filter on."""
    ui.seed_run("uir", "20260714-070000", "finished", summary="done")


def _names(ui_page):
    return ui_page.locator("table.list tbody tr td:first-child a").all_text_contents()


def test_the_active_column_reverses_on_a_second_click(ui, ui_page, make_routine):
    make_routine(slug="zzz")
    _seed_last_run(ui)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("table.list")

    head = ui_page.locator("table.list th", has_text="routine")
    head.click()
    expect(head).to_contain_text("▴")        # a new column starts at its natural direction
    first = _names(ui_page)

    head.click()
    expect(head).to_contain_text("▾")        # F208: the same column flips instead of no-opping
    assert _names(ui_page) == list(reversed(first))


def test_the_sort_choice_survives_a_reload(ui, ui_page, make_routine):
    """It is stored per browser: an operator who sorts by name means it next time too."""
    make_routine(slug="zzz")
    _seed_last_run(ui)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("table.list")
    ui_page.locator("table.list th", has_text="routine").click()
    expect(ui_page.locator("table.list th", has_text="routine")).to_contain_text("▴")

    ui_page.reload()
    ui_page.wait_for_selector("table.list")
    expect(ui_page.locator("table.list th", has_text="routine")).to_contain_text("▴")


def test_a_state_chip_filters_the_table(ui, ui_page):
    """The chips are a filter, not a sort: picking one that nothing matches empties the table
    and says so, rather than silently showing everything."""
    _seed_last_run(ui)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("table.list")
    assert _names(ui_page)

    ui_page.locator(".filterbar .tag", has_text="failed").click()
    expect(ui_page.locator(".empty")).to_be_visible()

    ui_page.locator(".filterbar .tag", has_text="failed").click()   # off again
    expect(ui_page.locator("table.list")).to_be_visible()
