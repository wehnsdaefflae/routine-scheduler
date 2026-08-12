"""Harness smoke: the console boots in a real browser — app shell renders, API auth via
the pre-seeded token works, and no uncaught JS error fires on the dashboard.
"""

import re

from playwright.sync_api import expect


def test_dashboard_renders(ui, ui_page):
    ui.seed_run("uir", "20260714-070000", "finished", summary="all done")
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1:has-text('Routines')", timeout=10_000)
    expect(ui_page.locator("body")).to_contain_text("Test uir", timeout=10_000)
    assert "rsched" in ui_page.title().lower()


def _day_width(ui_page):
    """The rendered width of one day column — the strip is pixel-true with a dynamic day
    width (two days fill the visible strip, the rest scroll), so scale expectations are
    computed from the layout instead of a pinned constant."""
    return ui_page.locator(".weekpanel svg.wg").bounding_box()["width"] / 7


def test_week_panel_shows_avg_runtime(ui, ui_page):
    """The "this week" strip renders each fire as a duration BAR whose width is the mean run
    wall-clock drawn true to scale against a day's width, floored at 2px; the routine legend
    sits below the timeline. 1800s + 1800s → avg 30m → (1800/86400) of a day column wide,
    and the legend names the row."""
    ui.seed_run("uir", "20260713-070000", "finished", elapsed_s=1800)
    ui.seed_run("uir", "20260714-070000", "finished", elapsed_s=1800)
    ui_page.goto(f"{ui.url}/#/routines")
    # uir's Monday cron puts it in the week grid; fires are bars, identity is in the legend below
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible(timeout=10_000)
    bar = ui_page.locator(".weekpanel .wg-bar").first
    expect(bar).to_be_visible(timeout=10_000)
    want = 1800 / 86400 * _day_width(ui_page)   # 30 min to scale against a 24h day column
    assert want > 2, "the expectation must sit above the 2px floor to prove the scale"
    assert abs(float(bar.get_attribute("width")) - want) < 0.5
    expect(ui_page.locator(".weekpanel .wg-legend")).to_contain_text("Test uir", timeout=10_000)


def test_week_panel_avg_is_5_run_moving_average(ui, ui_page):
    """F210: the week-strip bar is a 5-run MOVING average, so a stale long run outside the last
    five no longer drags the bar. Oldest run = 12h (43200s); the most recent five = 1800s each.
    A whole-window mean would be huge; the 5-run window keeps it at 30m to scale."""
    ui.seed_run("uir", "20260709-070000", "finished", elapsed_s=43200)   # stale, must be excluded
    for day in ("10", "11", "12", "13", "14"):
        ui.seed_run("uir", f"202607{day}-070000", "finished", elapsed_s=1800)
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible(timeout=10_000)
    bar = ui_page.locator(".weekpanel .wg-bar").first
    expect(bar).to_be_visible(timeout=10_000)
    # last 5 avg = 30m; the 12h run is outside the window (it would be ~14× wider)
    assert abs(float(bar.get_attribute("width")) - 1800 / 86400 * _day_width(ui_page)) < 0.5
    # the "over N runs" note lives in the legend link's hover title (not visible text)
    leg = ui_page.locator(".weekpanel .wg-leg").first
    expect(leg).to_have_attribute("title", re.compile(r"over 5 runs"), timeout=10_000)


def test_pause_scheduling_toggle(ui, ui_page):
    """D34: the dashboard's pause control drops the durable pause sentinel — the loud
    warn banner appears (owning the resume control, the head button hides), and resume
    clears it again. Run-now stays available throughout (option A semantics)."""
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1:has-text('Routines')", timeout=10_000)
    ui_page.click("button:has-text('pause scheduling')")
    expect(ui_page.locator(".panel.warn")).to_contain_text("Scheduling is paused", timeout=10_000)
    ui_page.click("button:has-text('resume scheduling')")
    expect(ui_page.locator("body")).not_to_contain_text("Scheduling is paused", timeout=10_000)
    expect(ui_page.locator("button:has-text('pause scheduling')")).to_be_visible(timeout=10_000)
