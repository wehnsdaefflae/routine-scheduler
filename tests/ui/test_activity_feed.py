"""The dashboard's activity feed (components/activityfeed.js) against the real console.

It polls while a run is active, so what it asks the daemon for — and how many of those asks
can pile up — is the whole of its cost. These tests pin that it reads the open-decisions count
from the shared questions store instead of fetching it, counts "open" the way the badge does,
keeps one load in flight at a time, and renders a run's transcript once however a row's
expand toggle is clicked.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect

from .conftest import until
from .helpers import watch_requests


def _ts(minutes_ago: int) -> str:
    """A run-ts inside the feed's default 7-day window (run-ts is always UTC)."""
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y%m%d-%H%M%S")


LIVE = _ts(2)
DONE = _ts(5)


def _open_feed(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    panel = ui_page.locator(".activity-panel")
    panel.locator("summary").click()
    expect(panel.locator(".logrow").first).to_be_visible()      # the first load landed
    return panel


def test_the_feed_never_fetches_the_decision_list_itself(ui, ui_page):
    """CLAUDE.md: one reader per endpoint — questions-store.js owns /api/questions. The feed's
    4 s poll used to ask for it every round, beside the badge's own fetch of the same list."""
    ui.seed_run("uir", LIVE, "running")
    _open_feed(ui, ui_page)
    polled = watch_requests(ui_page, re.compile(r"limit=300"))
    questions = watch_requests(ui_page, re.compile(r"/api/questions$"))
    ui_page.wait_for_timeout(4600)                               # > one 4 s poll round
    assert polled, "the poll never ran — this test would pass for the wrong reason"
    assert not questions, f"the feed fetched the decision list itself: {questions}"


def test_a_snoozed_decision_is_not_counted_as_open(ui, ui_page):
    """The header badge and the Decisions page count a snoozed decision as waiting silently,
    not as open; the feed's tile counted it."""
    ui.seed_run("uir", DONE, "finished", summary="done")
    ui.seed_question("uir", "q-open", "Which colour?")
    ui.seed_question("uir", "q-later", "Which font?",
                     extra={"snoozed_until": "2099-01-01T00:00:00+00:00"})
    panel = _open_feed(ui, ui_page)
    tile = panel.locator(".stat", has=ui_page.locator(".l", has_text="open decisions"))
    expect(tile.locator(".v")).to_have_text("1")


def test_one_load_is_in_flight_at_a_time(ui, ui_page):
    """A slow daemon must not collect a fresh batch of requests every poll round: while one
    load is outstanding, the next round waits for it."""
    ui.seed_run("uir", LIVE, "running")
    _open_feed(ui, ui_page)
    held = []

    def hold(route):        # Playwright wraps a Python function, never a bound builtin
        held.append(route)

    runs = re.compile(r"/api/runs\?limit=300")
    ui_page.route(runs, hold)
    until(lambda: held, what="the next poll round's load", page=ui_page)
    ui_page.wait_for_timeout(4600)                               # another poll round passes
    assert len(held) == 1, f"{len(held)} loads in flight at once"
    ui_page.unroute(runs)                 # releases the held request with the handler


def test_reopening_a_row_mid_fetch_renders_its_transcript_once(ui, ui_page):
    """Expand, collapse and expand again while the first transcript fetch is out: that fetch
    belongs to a transcript nobody is looking at, and must not land in the new one."""
    run_dir = ui.seed_run("uir", DONE, "finished", summary="done")
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "error",
                             "payload": {"where": "loop", "message": "one failure"}}) + "\n")
    panel = _open_feed(ui, ui_page)
    held = []

    def hold(route):
        held.append(route)

    ui_page.route(re.compile(rf"/api/runs/uir:{DONE}/transcript$"), hold)
    head = panel.locator(".logrow .rowhead").first
    head.click()                                                 # open: fetch #1 is held
    until(lambda: len(held) == 1, what="the first transcript fetch", page=ui_page)
    head.click()                                                 # closed
    head.click()                                                 # open again: fetch #2
    until(lambda: len(held) == 2, what="the second transcript fetch", page=ui_page)
    for route in held:
        route.continue_()
    body = panel.locator(".logbody").first
    expect(body.locator(".ev.error")).to_have_count(1)
    ui_page.wait_for_timeout(500)
    expect(body.locator(".ev.error")).to_have_count(1)
