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
    expect(banner.locator("button")).to_contain_text("reload the console")
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
