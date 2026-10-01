"""An older read never paints over a newer one.

A page that reloads on more than one trigger — a bus tick, a click, a filter — has more than one
read in flight whenever the daemon is slow, and responses do not come back in the order they
were asked for. Whichever answered LAST used to win, so a read that started before a change
could land after the read that showed it and quietly put the old state back — with nothing
left to correct it until some later trigger happened along.

Each test holds one response (captured at request time, so it carries the state of that moment)
until a newer read has rendered, then lets it land.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from .conftest import until


def _hold_first(page, matches, *, fetch=True):
    """Route the first request `matches(url)` accepts and keep it back — with its real response
    fetched NOW when `fetch`, so it carries the state of this moment. Returns the list the
    (route, response) pair lands in; every later request passes. Never unroute while one is
    held: Playwright releases a held request the moment its route goes."""
    held: list = []

    def handle(route):
        if held:
            route.continue_()
        else:
            held.append((route, route.fetch() if fetch else None))

    page.route(matches, handle)
    return held


def test_a_slow_bus_reload_does_not_undo_a_pause(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    pause = ui_page.locator("tr", has=ui_page.locator('a[href="#/routine/uir"]')).locator(
        'button[title^="pause this routine"]')
    expect(pause).to_be_visible()

    # a bus tick schedules a reload; its routine list is read while uir is still enabled
    held = _hold_first(ui_page, lambda url: url.endswith("/api/routines"))
    ui_page.evaluate("() => window.dispatchEvent(new CustomEvent('rsched-bus', "
                     "{ detail: { event: 'run_finished', run_id: 'x', state: 'finished' } }))")
    until(lambda: held, what="the bus reload's routine read", page=ui_page, timeout_s=8)

    pause.click()                                        # PATCH enabled:false, then a reload
    resume = ui_page.locator('button[title^="resume this routine"]')
    expect(resume).to_be_visible()

    route, stale = held[0]
    route.fulfill(response=stale)                        # the older read lands last
    ui_page.wait_for_timeout(800)
    expect(resume).to_be_visible()
    expect(ui_page.locator('button[title^="pause this routine"]')).to_have_count(0)


REPORT = {
    "schema": 1, "run_id": "self-audit:20260716-200000",
    "generated": "2026-07-16T20:00:00+00:00", "since": {"commit": "abc1234f", "window": "1 run"},
    "summary": "two items",
    "findings": [{"id": "F1", "severity": "problem", "title": "The thing is broken",
                  "detail": "x", "evidence": []}],
    "decisions": [{"id": "D1", "status": "open", "title": "Pick a path", "detail": "y",
                   "options": ["a", "b"]}],
}


def test_a_slow_filter_read_does_not_replace_the_one_you_chose(ui, ui_page, make_routine):
    make_routine(slug="self-audit")
    audit = ui.routines / "self-audit" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/messages?status=all&type=all")
    expect(ui_page.locator("#ref-F1")).to_be_visible()

    held = _hold_first(ui_page, lambda url: "/api/items?" in url and "type=finding" in url)
    ui_page.locator(".filterbar .tag", has_text="findings").click()
    until(lambda: held, what="the findings read", page=ui_page)
    ui_page.locator(".filterbar .tag", has_text="decisions").click()
    expect(ui_page.locator("#ref-D1")).to_be_visible()
    expect(ui_page.locator("#ref-F1")).to_have_count(0)

    route, stale = held[0]
    route.fulfill(response=stale)                        # the findings list answers last
    ui_page.wait_for_timeout(800)
    expect(ui_page.locator("#ref-D1")).to_be_visible()
    expect(ui_page.locator("#ref-F1")).to_have_count(0)


def test_a_bus_event_during_an_older_question_read_gets_a_fresh_one(ui, ui_page):
    """The questions store coalesces every surface's refresh into one fetch. An event that
    arrives while a read asked for BEFORE it is still in flight joined that read — whose answer
    may predate what the event announced — and nothing read again until the 30 s floor."""
    ui_page.goto(f"{ui.url}/#/help")
    ui_page.wait_for_selector("#view h1")
    ui_page.wait_for_timeout(500)                        # the boot reads have settled
    held = _hold_first(ui_page, lambda url: url.endswith("/api/questions"), fetch=False)
    sent: list[str] = []
    ui_page.on("request", lambda r: sent.append(r.url) if r.url.endswith("/api/questions") else None)

    ui_page.evaluate("() => { import('/static/questions-store.js')"
                     ".then((m) => m.loadQuestions().catch(() => {})); }")
    until(lambda: held, what="the explicit questions read", page=ui_page)
    ui_page.evaluate("() => window.dispatchEvent(new CustomEvent('rsched-bus', "
                     "{ detail: { event: 'run_state', state: 'waiting_user' } }))")
    held[0][0].continue_()
    ui_page.wait_for_timeout(3800)                       # past the store's 3 s window
    assert len(sent) == 2, f"the event was folded into a read that predates it: {sent}"
