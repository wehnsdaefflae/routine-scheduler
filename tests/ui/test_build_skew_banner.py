"""F696 — the console TELLS the operator when the page is newer than the server.

`/static` is served live from disk while every Python object stays at the build this process
booted with, and pydantic drops an unknown multipart field SILENTLY. The server publishes the
verdict (`build_skew` + `disk_build` on `/api/status`, `web/assetskew.py`), but a status key
nobody renders tells a person nothing — which is why this is a browser test that LOADS the
console and reads the banner, not an assertion about the payload.

The specimen is the operator's R2546: he set up everything on the conversation-creation page and
"most of the settings were gone" once the conversation started.
"""

import json
import re

from playwright.sync_api import expect

STATUS = re.compile(r"/api/status$")


def _status_saying(ui_page, **extra):
    """Serve `/api/status` with `extra` merged in, from the REAL payload — so the console gets
    the shape it actually gets, with only the skew keys moved."""
    def handler(route):
        got = route.fetch()
        body = got.json()
        body.update(extra)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui_page.route(STATUS, handler)


def test_a_newer_page_against_an_older_server_says_so_with_a_reload(ui, ui_page):
    _status_saying(ui_page, build="fab9f46 2026-10-19", disk_build="392379e 2026-10-19",
                   build_skew=True)
    ui_page.goto(f"{ui.url}/#/routines")

    banner = ui_page.locator("#skew-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("newer than the server")
    expect(banner).to_contain_text("392379e")           # what the console is running
    expect(banner).to_contain_text("fab9f46")           # what the server is
    expect(banner).to_contain_text("Reloading cannot fix this one")
    expect(banner.locator("button").first).to_contain_text("restart the server")
    ui_page.unroute(STATUS)


def test_the_primary_action_restarts_the_server_because_reloading_cannot_fix_it(ui, ui_page):
    """F745, operator 2026-10-22: "Clicking the button doesn't do anything."

    The banner offered `location.reload()`, and in THIS direction — a page newer than the
    server — reloading re-fetches the files the browser already has, so the banner comes
    straight back and the operator reasonably reads the button as dead. The stale half is the
    PROCESS, and only a daemon relaunch replaces it. So the primary button posts the same
    restart Settings posts, and says what stands between the request and the exit.
    """
    posted = []

    def restart_route(route):
        posted.append(route.request.method)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "active_runs": 2, "parked": 0}))

    ui_page.route(re.compile(r"/api/settings/restart$"), restart_route)
    _status_saying(ui_page, build="fab9f46 2026-10-19", disk_build="392379e 2026-10-19",
                   build_skew=True, restart_requested=False)
    ui_page.goto(f"{ui.url}/#/routines")

    banner = ui_page.locator("#skew-banner")
    expect(banner).to_be_visible()
    banner.locator("button").first.click()

    # it asked the SERVER to restart, and said what the wait is rather than going quiet
    expect(banner.locator("button").first).to_contain_text("restart requested")
    expect(banner).to_contain_text("queued behind 2 active run(s)")
    assert posted == ["POST"], posted
    ui_page.unroute(re.compile(r"/api/settings/restart$"))
    ui_page.unroute(STATUS)


def test_a_pending_restart_is_shown_instead_of_a_button_that_does_it_twice(ui, ui_page):
    """A restart already requested (the sentinel survives a page load) must not read as an
    untouched button: this is the state the operator was actually in — two sentinels pending
    while the daemon waited for a quiet gap."""
    _status_saying(ui_page, build="fab9f46 2026-10-19", disk_build="392379e 2026-10-19",
                   build_skew=True, restart_requested=True)
    ui_page.goto(f"{ui.url}/#/routines")

    banner = ui_page.locator("#skew-banner")
    expect(banner).to_be_visible()
    expect(banner.locator("button").first).to_contain_text("restart already requested")
    expect(banner.locator("button").first).to_be_disabled()
    expect(banner).to_contain_text("waiting for a quiet moment")
    ui_page.unroute(STATUS)


def test_no_banner_when_the_two_builds_agree(ui, ui_page):
    """The ordinary case, which is every moment but the minutes after a release: nothing is
    shown. A banner that is always up is a banner nobody reads."""
    _status_saying(ui_page, build="fab9f46 2026-10-19", disk_build="fab9f46 2026-10-19",
                   build_skew=False)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1:has-text('Routines')")

    expect(ui_page.locator("#skew-banner")).to_be_hidden()
    ui_page.unroute(STATUS)
