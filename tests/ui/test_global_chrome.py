"""Global chrome: the components mounted OUTSIDE the routed view, and the one property that
makes them chrome at all.

The navigation rail (index.html) and the bottom-right docks — the LLM activity dock
(components/taskmanager.js), the browser preview (components/browserdock.js) and the desktop
preview (components/desktopdock.js) — are siblings of #view rather than children of it, so they
survive navigation. None positions itself: `position: fixed` comes from base.css and nowhere
else — on the rail, and on the ONE #docks column the three docks ride. That makes those
stylesheet blocks the single point of failure, and losing one FAILS SILENTLY — the component
still builds, still fetches, still updates, and simply lands in the document flow at the foot of
every page. The 0.277.0 palette migration deleted two such blocks at once; the side TOC was found
three releases later, the dock twenty, each time by an operator looking at a screenshot.

So the invariant is pinned here rather than left to the eye: each piece of chrome is held to the
viewport, by its own `position: fixed` or by a fixed ancestor. The side TOC is no longer on the
list: it rides INSIDE the rail now, where losing its block leaves it stacked in view rather than
dropped off the foot of the page — test_toc.py owns it whole.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import expect

from .helpers import desktop, run_desktops

# (label, selector, route, a selector proving the route rendered, viewport width)
# The rail and the LLM dock are on every page at every width, so both are asserted on the
# narrowest one the console supports — where the rail is the bottom bar and the dock parks above
# it. The screen docks mount only once their screen is configured (the desktop dock: and a
# desktop runs), and only where there is a rail to sit beside (base.css hides them below 861px),
# so they are asserted at a laptop width with one set.
CHROME = [
    ("llm-dock", "#llm-tasks", "#/routines", "table.list", 390),
    ("nav-rail", ".topbar", "#/routines", "table.list", 390),
    ("browser-dock", "#browser-dock", "#/routines", "table.list", 1400),
    ("desktop-dock", "#desktop-dock", "#/routines", "table.list", 1400),
]

#: True when the element, or an ancestor, is `position: fixed` — what "held to the viewport"
#: means for a dock that rides the #docks column rather than being fixed itself.
FIXED = """e => { for (let n = e; n; n = n.parentElement)
                    if (getComputedStyle(n).position === 'fixed') return true;
                  return false; }"""


@pytest.mark.parametrize(("label", "selector", "route", "ready", "width"), CHROME,
                         ids=[c[0] for c in CHROME])
def test_global_chrome_is_positioned_by_the_stylesheet(ui, ui_page, monkeypatch, label,
                                                       selector, route, ready, width):
    if label == "browser-dock":
        ui.server_cfg.browser_view_url = "http://127.0.0.1:6080/vnc.html"
    if label == "desktop-dock":
        run_desktops(ui, monkeypatch, desktop("routines--uir", "a" * 32))
    ui_page.set_viewport_size({"width": width, "height": 900})
    ui_page.goto(f"{ui.url}/{route}")
    ui_page.wait_for_selector(ready)
    el = ui_page.locator(selector)
    expect(el).to_be_visible()
    assert el.evaluate(FIXED), (
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
    expect(dock).not_to_have_class(re.compile(r"\bsd-collapsed\b"))

    overlap = ui_page.evaluate(
        "() => { const d = document.querySelector('#browser-dock').getBoundingClientRect(),"
        " t = document.querySelector('.side-toc').getBoundingClientRect();"
        " return Math.min(d.right, t.right) - Math.max(d.left, t.left); }")
    assert overlap <= 0, f"the dock and the section index overlap by {overlap:.0f}px"


# ---- the bottom-right docks: one column, so opening one never covers another ----------------
#
# Operator-reported: the LLM chip, the browser chip and the desktop chip overlapped when opened.
# They were positioned one by one — the LLM dock in the corner, the browser dock on a fixed 44px
# offset above it — so the LLM panel, rising up to 520px, slid under an open browser preview.
# They are flex children of ONE fixed column now. This opens all three at once, each at its
# fullest (a screen frame mounted in both previews, as when their screens are live; a stack of
# LLM calls in the panel), and holds every pair of boxes apart — at the narrowest width that
# shows the previews, a laptop, and the wide layout where they rest open.

def _open_every_dock(page) -> None:
    for dock in ("#desktop-dock", "#browser-dock"):
        toggle = page.locator(f"{dock} .sd-toggle")
        if toggle.inner_text() == "show":
            toggle.click()
        # the dial settles first (nothing listens behind these screens), so the frame added
        # below is not swept away by its answer
        expect(page.locator(f"{dock} .sd-note")).to_contain_text("screen unreachable")
    # a connected screen is a frame: give each preview one, at its real size
    page.evaluate("""() => { for (const b of document.querySelectorAll('#docks .sd-body')) {
                               const f = document.createElement('iframe');
                               f.className = 'sd-frame'; b.prepend(f); } }""")
    if page.locator(".lt-pill").is_visible():      # the panel's open state is remembered
        page.locator(".lt-pill").click()
    page.evaluate("""() => {
      const fire = (d) => window.dispatchEvent(new CustomEvent("rsched-bus", { detail: d }));
      for (let i = 0; i < 14; i++) {
        fire({ event: "llm_task", status: "running", id: `t${i}`, phase: "started",
               endpoint: "e", model: "m", purpose: `call ${i}` });
      }
    }""")
    expect(page.locator(".lt-task")).to_have_count(14)


@pytest.mark.parametrize(("width", "height"), [(900, 700), (1280, 800), (1960, 1100)])
def test_open_docks_never_cover_each_other(ui, ui_page, monkeypatch, width, height):
    ui.server_cfg.browser_view_url = "http://127.0.0.1:1/vnc.html"
    run_desktops(ui, monkeypatch, desktop("routines--uir", "a" * 32))
    ui_page.set_viewport_size({"width": width, "height": height})
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("table.list")
    expect(ui_page.locator("#desktop-dock")).to_be_visible()
    expect(ui_page.locator("#browser-dock")).to_be_visible()
    _open_every_dock(ui_page)

    boxes = ui_page.evaluate("""() => ['#desktop-dock', '#browser-dock', '#llm-tasks'].map(s => {
        const r = document.querySelector(s).getBoundingClientRect();
        return { s, top: r.top, bottom: r.bottom, left: r.left, right: r.right }; })""")
    for i, a in enumerate(boxes):
        assert a["bottom"] - a["top"] > 20, f"{a['s']} is not open: {a}"
        assert a["top"] >= 0 and a["bottom"] <= height, f"{a['s']} leaves the viewport: {a}"
        for b in boxes[i + 1:]:
            across = min(a["right"], b["right"]) - max(a["left"], b["left"])
            down = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            assert across <= 0 or down <= 0, (
                f"{a['s']} and {b['s']} overlap by {across:.0f}x{down:.0f}px at {width}px: "
                f"{a} / {b}")


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


def test_a_resize_repaints_the_ribbon_from_the_runs_it_holds(ui, ui_page):
    """A resize changes the band's geometry and nothing it shows. It used to queue a refetch of
    200 runs — coalesced to one per 20 s — and until that landed the band stayed drawn for the
    old width, stretched, since its SVG scales without keeping its aspect."""
    ui.seed_run("uir", (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y%m%d-%H%M%S"),
                "finished", elapsed_s=120)
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".ribbon-summary")).to_contain_text("1 run in 24h")

    runs: list[str] = []
    ui_page.on("request", lambda r: runs.append(r.url) if "/api/runs" in r.url else None)
    ui_page.set_viewport_size({"width": 1100, "height": 900})
    ui_page.wait_for_timeout(1000)
    drawn = ui_page.evaluate("() => [document.querySelector('.ribbon-track').clientWidth,"
                             " document.querySelector('.ribbon-track svg').viewBox.baseVal.width]")
    assert drawn[0] == drawn[1], f"the band is drawn for {drawn[1]}px in a {drawn[0]}px track"
    expect(ui_page.locator(".ribbon-track .rb-run")).to_have_count(1)
    assert not runs, f"a resize refetched the run list: {runs}"


def test_the_group_row_says_what_is_happening_not_a_ratio(ui, ui_page):
    """D157-C: the group row SAYS the state instead of encoding it.

    `${done}/${children.length}` rendered a group of in-flight calls as `0/24` beside a panel
    header reading `24 running` — the same number counted the opposite way, both correct and
    neither informative (F572, from the operator's own screenshot). The operator chose: "the
    group row says what is happening ('24 running') instead of encoding it ('0/24')".

    Driven through the `rsched-bus` events the dock already listens on, so this asserts what the
    user SEES. Two details of the component the test has to respect: rendering is debounced 80ms,
    and a periodic `reconcile()` refetches /api/llm-tasks every 10s and would clear injected
    state — so the assertion uses the auto-retrying `expect`, which settles well inside that.
    """
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".lt-pill")).to_be_visible()
    ui_page.locator(".lt-pill").click()            # open the panel (the pill hides itself)
    ui_page.evaluate("""() => {
      const fire = (d) => window.dispatchEvent(new CustomEvent("rsched-bus", { detail: d }));
      fire({ event: "llm_process", phase: "opened", id: "p9", kind: "wizard", label: "Group" });
      for (const id of ["c1", "c2"]) {
        fire({ event: "llm_task", status: "running", id, phase: "started", process_id: "p9",
               endpoint: "e", model: "m", purpose: "Compaction \u00b7 archival" });
      }
    }""")
    row = ui_page.locator(".lt-proc-count").first
    expect(row).to_have_text("2 running")
    assert "/" not in row.inner_text(), "the row still encodes the state as a ratio"
