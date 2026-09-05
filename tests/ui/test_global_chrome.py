"""Global chrome: the components mounted OUTSIDE the routed view, and the one property that
makes them chrome at all.

The side table-of-contents (components/toc.js) and the LLM activity dock
(components/taskmanager.js) are siblings of #view rather than children of it, so they survive
navigation. Neither positions itself: `position: fixed` comes from base.css and nowhere else.
That makes their stylesheet block the single point of failure, and losing one FAILS SILENTLY —
the component still builds, still fetches, still updates, and simply lands in the document flow
at the foot of every page. The 0.277.0 palette migration deleted both blocks; the TOC was found
three releases later, the dock twenty, each time by an operator looking at a screenshot.

So the invariant is pinned here rather than left to the eye. test_toc.py still owns what the TOC
DOES (lists sections, jumps, hides on a narrow viewport); this file owns only the property whose
loss no other test can see.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

# (label, selector, route, a selector proving the route rendered, viewport width)
# The TOC needs the 1900px viewport its margin only exists above; the dock is on every page at
# every width, so it is asserted on the narrowest one the console supports.
CHROME = [
    ("llm-dock", "#llm-tasks", "#/routines", "table.list", 390),
    ("side-toc", ".side-toc", "#/settings", "#sec-connections", 1960),
]


@pytest.mark.parametrize(("label", "selector", "route", "ready", "width"), CHROME,
                         ids=[c[0] for c in CHROME])
def test_global_chrome_is_positioned_by_the_stylesheet(ui, ui_page, label, selector, route,
                                                       ready, width):
    ui_page.set_viewport_size({"width": width, "height": 900})
    ui_page.goto(f"{ui.url}/{route}")
    ui_page.wait_for_selector(ready, timeout=10_000)
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
