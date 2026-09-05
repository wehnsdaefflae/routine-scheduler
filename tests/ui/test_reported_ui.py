"""Operator-reported UI findings, reproduced against the REAL console.

Each test drives the same JS the daemon serves and asserts the reported symptom is absent, so it
is both the localization the finding was blocked on and the regression guard once fixed:

- F439 the mobile bottom nav must carry ALL NINE destinations, not the ~5 a phone showed.
- F432 the watch ribbon must paint a bar for a run in the last 24h (it read "always empty").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect

NAV = ".topbar nav a[data-nav]"


def _visible_in_viewport(ui_page, selector: str) -> int:
    """Count elements that are displayed AND sit inside the horizontal viewport — an item the
    bottom bar overflowed off-screen (the F439 symptom) is displayed but off to the right."""
    return ui_page.evaluate(
        """(sel) => [...document.querySelectorAll(sel)].filter((n) => {
            const r = n.getBoundingClientRect(); const s = getComputedStyle(n);
            return s.display !== "none" && s.visibility !== "hidden"
                && r.width > 0 && r.height > 0
                && r.left >= -1 && r.right <= window.innerWidth + 1;
        }).length""", selector)


def test_mobile_bottom_nav_shows_all_nine_destinations(ui, ui_page):
    ui_page.set_viewport_size({"width": 390, "height": 780})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(NAV).first).to_be_visible(timeout=10_000)
    # the DOM always carries nine; the reported bug is that only ~5 fit on the bottom bar
    expect(ui_page.locator(NAV)).to_have_count(9)
    assert _visible_in_viewport(ui_page, NAV) == 9


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
