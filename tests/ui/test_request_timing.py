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
import re

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
    # A VISIBLE tab must NOT be marked. Without this, a guard that marked every row would pass
    # both this test and the hidden-tab one below, and every real measurement in the store
    # would read as unusable wall clock.
    assert "backgrounded" not in row["detail"], row


def test_a_slow_request_measured_in_a_hidden_tab_says_so(ui, ui_page):
    """The `slow` kind's half of F863, which the load kind got in 0.525.5 and this one did not.

    MEASURED in production: 24 client rows claiming 106,205-183,081 ms were all REPORTED inside
    one 3-second window (2026-11-10 12:35:58-12:36:01 UTC, every one status 200) — a tab
    flushing its queue on wake, `performance.now()` having counted the laptop's sleep. No SERVER
    row in that store exceeds 16,790 ms.

    `performance.now()` keeps running while a tab is hidden (it is a monotonic clock, not a work
    meter), so the duration cannot be corrected — only DISCLOSED. The row is kept and marked:
    a slow request nobody was watching is still evidence about the server, and dropping it would
    bias the store toward whatever the operator happens to be looking at.

    Same technique and same limit as the load case below: this pins OUR branch, not the
    browser's own clock behaviour.
    """
    ui_page.add_init_script(
        "Object.defineProperty(document, 'visibilityState', "
        "{configurable: true, get: () => 'hidden'});")
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar", state="attached")

    def hold(route):
        ui_page.wait_for_timeout(HELD_MS)
        route.continue_()

    ui_page.route("**/api/stats**", hold)
    ui_page.evaluate("""() => { import('/static/api.js').then(
        (m) => m.api('/api/stats?window=7d').catch(() => {})); }""")

    until(lambda: _rows(ui, "slow"), what="the slow-request trace to be stored", page=ui_page,
          timeout_s=30)
    (row, *_) = _rows(ui, "slow")
    assert "backgrounded (wall clock, not server time)" in row["detail"], row
    # the row still carries everything it carried before — the marker ADDS, it does not replace
    assert row["target"] == "/api/stats", row
    assert "status 200" in row["detail"] and "streams open" in row["detail"], row
    assert re.match(r"\d+ms", row["detail"]), row


def test_a_navigation_becomes_a_render_row_naming_the_view(ui, ui_page):
    """F861: the cost this console mostly is — a hash navigation, measured until #view settles.

    Only the first page load fires `load`, so every click through the nav is invisible to the
    other two duration kinds unless a request happens to cross the 1,000 ms floor. This drives a
    REAL navigation in the real console and asserts the row exists, names the view it rendered,
    and carries a plausible number.
    """
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar")
    ui_page.click("#nav a[href='#/routines']")
    ui_page.wait_for_selector("#view h1")

    until(lambda: _rows(ui, "render"), what="the render trace to be stored", page=ui_page,
          timeout_s=30)
    (row, *_) = _rows(ui, "render")
    assert row["view"] == "routines", row            # which page was rendered
    ms = int(row["detail"].split("ms", 1)[0])
    assert 0 <= ms < 30000, row                      # a real elapsed, inside the ceiling


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
    # A VISIBLE load must carry the number. The substring `load ` alone is also satisfied by
    # `load unavailable (backgrounded)`, so without this the suppression below could silence
    # every row and this test would still pass.
    load_ms = int(row["detail"].rsplit("load ", 1)[1].split("ms", 1)[0])
    assert 0 <= load_ms < 120000, row


def test_a_backgrounded_tab_reports_no_load_milestone_rather_than_an_hour(ui, ui_page):
    """F863: a tab backgrounded while loading does not finish loading — the browser defers the
    `load` event until it is looked at again, so `loadEventEnd` measures how long it sat in
    another window. The first production reading of this instrument held a 4,271,306 ms
    (71-minute) "page load" for exactly that reason, and nothing downstream can tell such a
    number from a real one.

    So the milestone is withheld, not faked. `response` and `dom` stay, because both are
    normally past before a tab can be switched away from, and the reader parses `load <n>ms` —
    which simply does not match.
    """
    # `document.visibilityState` is the property the instrument reads, so that is what is
    # overridden — installed before any of the page's own script runs. The CDP route
    # (`Emulation.setPageVisibilityOverride`) is NOT available in this Chromium build: it
    # answers `wasn't found`, which is why this drives the property directly. What that means
    # for what this test proves is stated plainly: it pins OUR branch (a hidden document yields
    # no load number), not the browser's deferral of `load` itself, which is upstream behaviour
    # this suite cannot and need not reproduce.
    ui_page.add_init_script(
        "Object.defineProperty(document, 'visibilityState', "
        "{configurable: true, get: () => 'hidden'});")
    ui_page.goto(ui.url)
    ui_page.wait_for_selector(".topbar", state="attached")
    until(lambda: _rows(ui, "load"), what="the page-load trace to be stored", page=ui_page,
          timeout_s=30)

    (row, *_) = _rows(ui, "load")
    assert "load unavailable (backgrounded)" in row["detail"], row
    assert "response " in row["detail"] and "dom " in row["detail"], row
    # the shape the audit reader looks for must be ABSENT, so no median can absorb it
    assert not re.search(r"load \d+ms", row["detail"]), row
