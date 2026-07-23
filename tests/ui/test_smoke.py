"""Harness smoke: the console boots in a real browser — app shell renders, API auth via
the pre-seeded token works, and no uncaught JS error fires on the dashboard.
"""

from playwright.sync_api import expect


def test_dashboard_renders(ui, ui_page):
    ui.seed_run("uir", "20260714-070000", "finished", summary="all done")
    ui_page.goto(ui.url)
    ui_page.wait_for_selector("h1:has-text('Routines')", timeout=10_000)
    expect(ui_page.locator("body")).to_contain_text("Test uir", timeout=10_000)
    assert "rsched" in ui_page.title().lower()


def test_week_panel_shows_avg_runtime(ui, ui_page):
    """The "this week" strip renders each fire as a duration BAR whose width is the mean run
    wall-clock drawn true to scale against a day's width (24h = DAY_W 144 → 6px/hour), floored at
    2px; the routine legend sits below the timeline. 1800s + 1800s → avg 30m → (1800/86400)*144 =
    3px wide, and the legend names the row."""
    ui.seed_run("uir", "20260713-070000", "finished", elapsed_s=1800)
    ui.seed_run("uir", "20260714-070000", "finished", elapsed_s=1800)
    ui_page.goto(ui.url)
    # uir's Monday cron puts it in the week grid; fires are bars, identity is in the legend below
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible(timeout=10_000)
    bar = ui_page.locator(".weekpanel .wg-bar").first
    expect(bar).to_be_visible(timeout=10_000)
    assert bar.get_attribute("width") == "3"   # 30 min to scale against a 24h day column
    expect(ui_page.locator(".weekpanel .wg-legend")).to_contain_text("Test uir", timeout=10_000)


def test_pause_scheduling_toggle(ui, ui_page):
    """D34: the dashboard's pause control drops the durable pause sentinel — the loud
    warn banner appears (owning the resume control, the head button hides), and resume
    clears it again. Run-now stays available throughout (option A semantics)."""
    ui_page.goto(ui.url)
    ui_page.wait_for_selector("h1:has-text('Routines')", timeout=10_000)
    ui_page.click("button:has-text('pause scheduling')")
    expect(ui_page.locator(".panel.warn")).to_contain_text("Scheduling is paused", timeout=10_000)
    ui_page.click("button:has-text('resume scheduling')")
    expect(ui_page.locator("body")).not_to_contain_text("Scheduling is paused", timeout=10_000)
    expect(ui_page.locator("button:has-text('pause scheduling')")).to_be_visible(timeout=10_000)
