"""Where the activity feed IS: mounted on Messages, absent from Routines, and silent until
opened — a pair of assertions, because a move that leaves the old mount in place is not a move.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import expect

from .helpers import watch_requests

PANEL = "details.activity-panel"


def test_the_routines_page_no_longer_carries_the_activity_feed(ui, ui_page):
    ui.seed_run("uir", "20260714-070000", "finished", summary="all done")
    ui_page.goto(f"{ui.url}/#/routines")
    # the page itself rendered, so a count of 0 below means absence and not an unpainted view
    expect(ui_page.locator("table.list, .empty, .weekpanel").first).to_be_visible()
    expect(ui_page.locator(PANEL)).to_have_count(0)
    expect(ui_page.locator(".activity-panel .feed")).to_have_count(0)


def test_the_messages_page_carries_it(ui, ui_page):
    ui.seed_run("uir", "20260714-070000", "finished", summary="all done")
    ui_page.goto(f"{ui.url}/#/messages")
    panel = ui_page.locator(PANEL)
    expect(panel).to_have_count(1)
    expect(panel.locator("summary")).to_contain_text("activity — every run across every routine")
    # ...beside the hook panel, which came here first: both readings, one page
    expect(ui_page.locator("details.hookact-panel")).to_have_count(1)


def test_it_fetches_nothing_until_opened_and_then_lists_runs(ui, ui_page):
    """The move must not cost the laziness, and laziness here is about FETCHING, not markup:
    `activityFeed()` builds its node eagerly (the filter bar exists from the start, collapsed
    inside the `details`), while `start()` — the first load and the 4 s poll over three
    endpoints — waits for the section to open. The feed's signature request is the 300-run
    window, which nothing else on this page asks for.
    """
    ts = time.strftime("%Y%m%d-070000")
    ui.seed_run("uir", ts, "finished", summary="all done")
    before = watch_requests(ui_page, re.compile(r"limit=300"))
    ui_page.goto(f"{ui.url}/#/messages")
    panel = ui_page.locator(PANEL)
    expect(panel).not_to_have_attribute("open", "")
    expect(panel.locator("summary")).to_be_visible()         # the page settled, collapsed
    ui_page.wait_for_timeout(4600)                           # > one poll round, had it armed
    assert not before, f"the collapsed feed fetched anyway: {before}"

    panel.locator("summary").click()
    expect(panel.locator(".logbar")).to_be_visible()
    expect(panel.locator(".feed")).to_contain_text("uir")
