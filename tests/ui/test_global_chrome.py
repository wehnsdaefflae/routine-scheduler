"""Global chrome: the components mounted OUTSIDE the routed view, and the one property that
makes them chrome at all.

The navigation rail (index.html) and the LLM activity dock (components/taskmanager.js) are
siblings of #view rather than children of it, so they survive navigation. Neither positions
itself: `position: fixed` comes from base.css and nowhere else. That makes their stylesheet
block the single point of failure, and losing one FAILS SILENTLY — the component still builds,
still fetches, still updates, and simply lands in the document flow at the foot of every page.
The 0.277.0 palette migration deleted two such blocks at once; the side TOC was found three
releases later, the dock twenty, each time by an operator looking at a screenshot.

So the invariant is pinned here rather than left to the eye. The side TOC is no longer on the
list: it rides INSIDE the rail now, where losing its block leaves it stacked in view rather than
dropped off the foot of the page — test_toc.py owns it whole.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import expect

# (label, selector, route, a selector proving the route rendered, viewport width)
# Both are on every page at every width, so both are asserted on the narrowest one the console
# supports — where the rail is the bottom bar and the dock parks above it.
CHROME = [
    ("llm-dock", "#llm-tasks", "#/routines", "table.list", 390),
    ("nav-rail", ".topbar", "#/routines", "table.list", 390),
]


@pytest.mark.parametrize(("label", "selector", "route", "ready", "width"), CHROME,
                         ids=[c[0] for c in CHROME])
def test_global_chrome_is_positioned_by_the_stylesheet(ui, ui_page, label, selector, route,
                                                       ready, width):
    ui_page.set_viewport_size({"width": width, "height": 900})
    ui_page.goto(f"{ui.url}/{route}")
    ui_page.wait_for_selector(ready)
    el = ui_page.locator(selector)
    expect(el).to_be_visible()
    assert el.evaluate("e => getComputedStyle(e).position") == "fixed", (
        f"{label} lost its base.css block — it is in the document flow, not the viewport")


def test_the_llm_dock_wears_the_design_system(ui, ui_page):
    """The symptom the missing block actually produced: a bare UA <button> under the last card.
    A styled pill is round, mono (everything the dock renders was emitted by a counter) and sits
    on a plate — none of which a browser gives a button for free."""
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    pill = ui_page.locator(".lt-pill")
    expect(pill).to_be_visible()
    style = pill.evaluate("e => { const c = getComputedStyle(e);"
                          " return {r: c.borderRadius, f: c.fontFamily, bg: c.backgroundColor}; }")
    assert style["r"].startswith("20px"), f"pill is not round: {style['r']}"
    assert "mono" in style["f"], f"pill is not mono: {style['f']}"
    assert style["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), "pill has no plate under it"


def test_the_llm_dock_clears_the_phone_bottom_bar(ui, ui_page):
    """On a phone the navigation rail becomes the bottom bar, so the corner the dock parks in is
    taken. Restoring the pre-rail `bottom: 12px` would drop the dock onto the nav icons."""
    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".lt-pill")).to_be_visible()
    gap = ui_page.evaluate(
        "() => document.querySelector('.topbar').getBoundingClientRect().top"
        " - document.querySelector('.lt-pill').getBoundingClientRect().bottom")
    assert gap > 0, f"the dock overlaps the bottom nav bar by {-gap:.0f}px"


# ---- the browser dock: chrome that is also an OVERLAY ---------------------------------------
#
# WHERE the dock rests, and what it shows when the screen is unreachable, live in
# test_browser_screen.py with the rest of that feature. What belongs HERE is the one thing
# neither file could see alone: what two pieces of fixed chrome do to each other.

def test_the_dock_and_the_section_index_no_longer_contend(ui, ui_page):
    """They used to share the right margin: `.side-toc` spanned top:118px → bottom:90px and an
    open `#browser-dock` rose off a 60px offset, so the dock covered the index's last four or
    five links and the index had to reserve the dock's band. The index moved into the rail, so
    the two are on opposite edges — and the geometry says so rather than a comment."""
    ui.server_cfg.browser_view_url = "http://127.0.0.1:6080/vnc.html"
    ui_page.set_viewport_size({"width": 1960, "height": 950})
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.wait_for_selector("#sec-connections")
    dock, toc = ui_page.locator("#browser-dock"), ui_page.locator(".side-toc")
    expect(dock).to_be_visible()
    expect(toc).to_be_visible()
    expect(dock).not_to_have_class(re.compile(r"\bbd-collapsed\b"))

    overlap = ui_page.evaluate(
        "() => { const d = document.querySelector('#browser-dock').getBoundingClientRect(),"
        " t = document.querySelector('.side-toc').getBoundingClientRect();"
        " return Math.min(d.right, t.right) - Math.max(d.left, t.left); }")
    assert overlap <= 0, f"the dock and the section index overlap by {overlap:.0f}px"


# ---- the watch ribbon: the sentence every page carries ---------------------------------------

def test_the_ribbon_summary_counts_are_ways_in(ui, ui_page):
    """"1 failed" rode every page as plain text, so the only route to the run it counted was a
    ~6px red tick in a ~900px band. Each count that names runs links to the newest of them."""
    ts = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y%m%d-%H%M%S")
    ui.seed_run("uir", ts, "failed", summary="it broke", elapsed_s=120)
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    link = ui_page.locator('.ribbon-summary a[href^="#/run/"]')
    expect(link).to_have_text("1 failed")
    link.click()
    ui_page.wait_for_url(f"{ui.url}/#/run/uir:{ts}")
