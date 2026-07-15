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
