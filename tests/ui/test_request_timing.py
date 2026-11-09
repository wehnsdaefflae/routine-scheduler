"""F858: the real console, a really slow request, and a row carrying how long it took.

The fast suite pins the store and the source. This drives what the operator actually loads: the
shipped `trace.js` installed by `app.js`, a real `fetch` through `api.js`'s one authed-fetch
loop, a response genuinely held past the floor, the real POST and the real day file.

It has to be a browser test. The quantity is produced by `performance.now()` around a fetch the
browser performed and by the browser's own Navigation Timing entry — neither exists under
pytest, so a fast test can only assert that the source says it does. The thing that would break
in production is the instrument not being MOUNTED, and only a loaded page can show that.
"""

from __future__ import annotations

import json

from .conftest import until

HELD_MS = 1800          # over trace.js's SLOW_REQUEST_MS floor of 1000, with room for jitter


def _rows(ui, kind: str) -> list[dict]:
    return [r for p in sorted((ui.routines / ".ui-traces").glob("*.jsonl"))
            for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()
            for r in [json.loads(ln)] if r.get("kind") == kind]


def test_a_slow_request_becomes_a_row_naming_the_endpoint_and_the_wait(ui, ui_page):
    """A request held 1.8 s is recorded as a `slow` row: which endpoint, how many ms, what else
    was open. This is the row a "where is the console slow" question is answered from.
    """
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")

    def hold(route):
        # Playwright's sync route handler runs on this thread: sleeping in it delays the
        # RESPONSE the page is waiting for, which is exactly the latency being measured.
        ui_page.wait_for_timeout(HELD_MS)
        route.continue_()

    ui_page.route("**/api/stats**", hold)
    # Not awaited: the page stays responsive while the request hangs, like the real thing.
    ui_page.evaluate("""() => { import('/static/api.js').then(
        (m) => m.api('/api/stats?window=7d').catch(() => {})); }""")

    until(lambda: _rows(ui, "slow"), what="the slow-request trace to be stored", page=ui_page,
          timeout_s=30)
    (row, *_) = _rows(ui, "slow")
    assert row["target"] == "/api/stats", row          # the PATH, and the query stripped off it
    assert "window=7d" not in json.dumps(row)          # a query value never reaches the store
    held = int(row["detail"].split("ms", 1)[0])
    assert held >= HELD_MS * 0.6, row                  # the wait itself, not a token round-trip
    assert "status 200" in row["detail"], row
    assert "streams open" in row["detail"], row


def test_the_page_load_becomes_a_row_with_its_milestones(ui, ui_page):
    """One `load` row per page load, carrying the three Navigation Timing milestones — so the
    cost of the page becoming usable is attributable to the server, the JS or the assets.
    """
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")

    until(lambda: _rows(ui, "load"), what="the page-load trace to be stored", page=ui_page,
          timeout_s=30)
    (row, *_) = _rows(ui, "load")
    for milestone in ("response ", "dom ", "load "):
        assert milestone in row["detail"], (milestone, row)
    dom = int(row["detail"].split("dom ", 1)[1].split("ms", 1)[0])
    assert dom > 0, row                                # a real measurement, not a zeroed entry
