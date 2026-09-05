"""Operator-reported UI findings, reproduced against the REAL console.

Each test drives the same JS the daemon serves and asserts the reported symptom is absent, so it
is both the localization the finding was blocked on and the regression guard once fixed:

- F432 the watch ribbon must paint a bar for a run in the last 24h (it read "always empty").
- F434 the routines view must re-fetch its cards on nav-back.

F439 (the mobile bottom nav) MOVED to tests/ui/test_mobile_nav.py. Its guard here counted nav
items against `window.innerWidth` and so could never fail: on a phone that value is the layout
viewport, which is precisely what the overflowing document expands. The replacement clips against
the width the test emulated and asserts the document itself never scrolls sideways.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect

NAV = ".topbar nav a[data-nav]"


def test_watch_ribbon_paints_a_bar_for_a_recent_run(ui, ui_page):
    ts = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y%m%d-%H%M%S")
    ui.seed_run("uir", ts, "finished", summary="ok", elapsed_s=600)
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    # the ribbon polls /api/runs on mount; the seeded run sits inside the last-24h window
    expect(ui_page.locator(".ribbon-track svg .rb-run").first).to_be_visible(timeout=10_000)


def test_routines_view_refetches_cards_on_nav_back(ui, ui_page):
    """F434: the routine cards' unread/to-do marks must re-fetch when you navigate back into the
    routines view — a hashchange re-render, not a stale paint that needs a manual refresh."""
    calls: list[int] = []
    ui_page.on("request", lambda r: calls.append(1)
               if r.method == "GET" and "/api/routines" in r.url else None)
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(NAV).first).to_be_visible(timeout=10_000)
    ui_page.wait_for_timeout(400)
    before = len(calls)
    ui_page.locator('.topbar nav a[data-nav="messages"]').click()
    ui_page.wait_for_timeout(200)
    ui_page.locator('.topbar nav a[data-nav="dashboard"]').click()
    ui_page.wait_for_timeout(700)
    assert len(calls) > before, (before, len(calls))
