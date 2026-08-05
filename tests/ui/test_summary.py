"""Summary tab: each routine's latest finish message renders (markdown → real DOM), links to
its run, and can be dismissed (mark read) with the state persisting across a reload — driven
in a real browser against the stub-runner console."""

import re

from playwright.sync_api import expect


def test_summary_shows_latest_message_and_marks_read(ui, ui_page):
    ui.seed_run("uir", "20260714-070000", "finished",
                summary="## did the thing\n\nall **green** today")
    ui_page.goto(f"{ui.url}/#/summary")
    ui_page.wait_for_selector("h1:has-text('Summary')", timeout=10_000)

    # Unread is the default view (operator, 2026-08-05): its chip is active on load.
    expect(ui_page.get_by_role("button", name="Unread")).to_have_class(re.compile(r"\bactive\b"), timeout=10_000)

    # the finish message renders (block markdown → real DOM, not raw text)
    item = ui_page.locator(".summary-item")
    expect(item).to_contain_text("did the thing", timeout=10_000)
    expect(item).to_contain_text("all green today")

    # a jump-to-run link points at the run page
    expect(item.locator("a[href='#/run/uir:20260714-070000']")).to_be_visible()

    # mark read → in the default Unread view the item drops out of the list
    ui_page.get_by_role("button", name="mark read").click()
    expect(ui_page.locator(".summary-item")).to_have_count(0, timeout=10_000)

    # reload: still on Unread by default, still empty (the dismissal persisted)
    ui_page.reload()
    ui_page.wait_for_selector("h1:has-text('Summary')", timeout=10_000)
    expect(ui_page.locator(".summary-item")).to_have_count(0, timeout=10_000)

    # switch to All → the read item is still there, now showing "mark unread"
    ui_page.get_by_role("button", name="All").click()
    expect(ui_page.get_by_role("button", name="mark unread")).to_be_visible(timeout=10_000)
