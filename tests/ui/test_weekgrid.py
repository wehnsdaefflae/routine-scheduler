"""The dashboard "this week" strip: the live green "now" cursor advances on its own between
data refreshes (F230); the routines of one LANE merge onto a single labelled strip row — a
SCHEDULED lane's row chains its members at the LANE's fires (D71/R313); and bars drag: onto a
sibling to reorder the lane, onto another lane's row to join it, onto the remove strip to
leave, and along the own row to reschedule.

The strip calls its own rows "lanes" too (`.wg-row` / `.wg-lane-label`) — that is a geometry
word this component has always used and it is NOT the routine lane, so the assertions below
read the label's TEXT (a lane's row is prefixed ⛓) rather than any class modifier.
"""

import re
from datetime import datetime

from playwright.sync_api import expect

from .conftest import until

# ---- pointer-gesture helpers (weekgrid-drag.js) ------------------------------------------

def _center(loc):
    b = loc.bounding_box()
    assert b, "element has no bounding box"
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def _drag(page, from_xy, to_xy):
    """One pointer gesture, with a detour first so the controller's 5px click-vs-drag
    threshold is crossed long before the drop point (which may sit close to the grab)."""
    page.mouse.move(*from_xy)
    page.mouse.down()
    page.mouse.move(from_xy[0] + 30, from_xy[1] - 6, steps=3)
    page.mouse.move(*to_xy, steps=6)
    page.mouse.up()


def _scroll_strip_to(ui_page, bar, margin=60):
    """Scroll the two-day week strip so `bar` sits near the visible left edge — a fire later
    in the week is laid out but outside the viewport until scrolled to."""
    scroll = ui_page.locator(".weekpanel .wg-scroll")
    delta = bar.bounding_box()["x"] - scroll.bounding_box()["x"] - margin
    scroll.evaluate("(el, dx) => { el.scrollLeft += dx }", delta)


def test_now_cursor_advances_on_its_own(ui, ui_page):
    """With no data refresh, the wg-now cursor re-positions itself on the component's internal
    timer. Driven by Playwright's fake clock so it is deterministic (no 30s real wait): capture
    the cursor's x, fast-forward two hours, and assert it moved to the right."""
    # Install the fake clock BEFORE any script runs, frozen at 08:00 TODAY (real date, fixed
    # morning hour). Keeping the real DATE means the browser's day columns and now-cursor still
    # align with the server's fire computation (fires land in the 7-day window from today's
    # midnight, day-0 is still TODAY); pinning the HOUR to the morning means the +2h fast-forward
    # below can never cross local midnight. The old `install()` froze at the real wall-clock time,
    # so any run started ~22:00–00:00 local wrapped past midnight — the week strip re-anchors day-0
    # to the new day and the now-cursor jumps to the left edge, reding this "moves right" assertion
    # (F241: self-audit's own 01:00 nightly gate hit it deterministically).
    morning = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    ui_page.clock.install(time=morning)
    ui_page.goto(f"{ui.url}/#/routines")

    # the week panel is open by default; its now-cursor renders once a scheduled routine is in view.
    # An SVG <line> has no box, so Playwright treats it as "hidden" — wait for ATTACHED, not visible.
    now = ui_page.locator("line.wg-now")
    now.first.wait_for(state="attached")
    x_before = float(now.first.get_attribute("x1"))

    # advance two hours: Date.now() moves forward and the component's own interval fires, which
    # re-renders the strip from the SAME data — only the now-cursor (and past/future dimming) moves
    ui_page.clock.fast_forward("02:00:00")

    # the cursor moved right; two hours of a seven-day span is ~12px on the 1008px strip, well
    # above any rounding — a frozen cursor (the bug) would leave x1 unchanged. Poll for the
    # re-render (the interval fires under the advanced clock) before the hard assertion.
    x_after = x_before
    for _ in range(50):
        x_after = float(ui_page.locator("line.wg-now").first.get_attribute("x1"))
        if x_after > x_before + 2:
            break
        ui_page.wait_for_timeout(200)
    assert x_after > x_before + 2, f"now-cursor did not advance: {x_before} -> {x_after}"


def test_same_lane_routines_share_one_week_row(ui, ui_page, make_routine):
    """F271 (operator ask): routines in the same lane are drawn on the SAME week-strip row, in
    the lane's member (execution) order — so a lane reads as one chain on the timeline rather
    than scattered across separate rows. Two scheduled routines in one lane → ONE wg-row
    carrying both their bars; a routine in no lane keeps its own row."""
    from rsched import lanes

    # uir already exists (ui fixture); add a second scheduled routine and a solo one.
    make_routine(slug="uir2")
    make_routine(slug="solo")
    # uir + uir2 share a lane, in fire order; 'solo' is in none.
    lanes.create(ui.routines, name="Nightly", on_failure="stop",
                 members=[{"slug": "uir"}, {"slug": "uir2"}])

    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible()
    rows = ui_page.locator(".weekpanel .wg-row")
    # The lane's two routines collapse into ONE row; solo has its own → 2 rows, not 3.
    expect(rows).to_have_count(2)
    # The merged row carries bars linking to BOTH of the lane's routines.
    lane_row = ui_page.locator(".weekpanel .wg-row",
                               has=ui_page.locator("a[href='#/routine/uir2']")).first
    expect(lane_row.locator("a[href='#/routine/uir'] .wg-bar")).to_have_count(1)
    expect(lane_row.locator("a[href='#/routine/uir2'] .wg-bar")).to_have_count(1)
    # The row names itself in the name column (the legend is retired — color identity
    # lives on the routine rows/cards as swatches).
    expect(ui_page.locator(".weekpanel .wg-lane-label",
                           has_text="⛓ Nightly")).to_have_count(1)


def test_a_row_label_keys_its_own_bar_colours(ui, ui_page, make_routine):
    """The strip's bars are identity colours, not outcome colours, and the only thing that said
    so was the swatch on a routine's table row — which a LANE row does not have and which the
    collapsed-by-default lane rows never showed. Each strip row now opens with its members'
    swatches, in fire order, in the hue `slugColor` gives them."""
    from rsched import lanes

    make_routine(slug="uir2")
    lanes.create(ui.routines, name="Nightly", on_failure="stop",
                 members=[{"slug": "uir"}, {"slug": "uir2"}])
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible()
    label = ui_page.locator(".weekpanel .wg-lane-label", has_text="⛓ Nightly")
    swatches = label.locator(".id-swatch")
    expect(swatches).to_have_count(2)          # one per member, fire order
    # the same hash the bars use (charts.slugColor), so the key and the bar agree
    colours = swatches.evaluate_all("els => els.map(e => getComputedStyle(e).backgroundColor)")
    bar = ui_page.locator(".weekpanel a[href='#/routine/uir'] .wg-bar").first
    assert bar.evaluate("e => getComputedStyle(e).fill") == colours[0], (
        f"the first member's key {colours[0]} is not its bar colour")
    assert colours[0] != colours[1], "two members share one key colour"
    # the name is still readable beside the key
    expect(label.locator(".wg-name")).to_have_text("⛓ Nightly")


def _chained_lane(ui, make_routine):
    """Two fresh routines in a SCHEDULED lane 'Chained' (daily 09:30 — the members' own
    weekly fixture crons are suppressed, D71); the harness routine 'uir' stays beside it
    on a row of its own, in no lane."""
    from rsched import lanes

    make_routine(slug="gm1")
    make_routine(slug="gm2")
    return lanes.create(ui.routines, name="Chained", on_failure="stop", cron="30 9 * * *",
                        members=[{"slug": "gm1"}, {"slug": "gm2"}])


def _members(ui, lane_id):
    from rsched import lanes

    rec = lanes.get(ui.routines, lane_id)
    return [m["slug"] for m in (rec["members"] if rec else [])]


def test_scheduled_lane_chains_on_one_labelled_row(ui, ui_page, make_routine):
    """D71/R313: a lane WITH a cron owns its members' schedule. The row draws the LANE's
    fires — each member once per fire, chained — so both members carry the SAME bar count,
    the daily count, not their own (suppressed) weekly crons' 1-2. Every row names itself
    in the name column; the lane label's hover carries the lane's schedule."""
    _chained_lane(ui, make_routine)

    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible()
    # two rows: the lane's + the solo harness routine's — each labelled
    expect(ui_page.locator(".weekpanel .wg-row")).to_have_count(2)
    expect(ui_page.locator(".weekpanel .wg-lane-label", has_text="Test uir")).to_have_count(1)
    lane_label = ui_page.locator(".weekpanel .wg-lane-label", has_text="⛓ Chained")
    expect(lane_label).to_have_count(1)
    # the row's schedule is the LANE's, not the vestigial member cron
    expect(lane_label).to_have_attribute("title", re.compile("09:30"))
    n1 = ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").count()
    n2 = ui_page.locator(".weekpanel a[href='#/routine/gm2'] .wg-bar").count()
    assert n1 == n2 and 6 <= n1 <= 8, f"expected the daily lane fires on both members, got {n1}/{n2}"


def test_drag_onto_sibling_reorders_the_lane(ui, ui_page, make_routine):
    g = _chained_lane(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel a[href='#/routine/gm2'] .wg-bar").first).to_be_visible()

    src = _center(ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").first)
    tgt = _center(ui_page.locator(".weekpanel a[href='#/routine/gm2'] .wg-bar").first)
    _drag(ui_page, src, (tgt[0] + 4, tgt[1]))   # right half of the sibling → "after gm2"
    until(lambda: _members(ui, g["id"]) == ["gm2", "gm1"], what="reorder")


def test_drag_to_remove_strip_leaves_the_lane(ui, ui_page, make_routine):
    g = _chained_lane(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routines")
    bar = ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").first
    expect(bar).to_be_visible()

    src = _center(bar)
    ui_page.mouse.move(*src)
    ui_page.mouse.down()
    ui_page.mouse.move(src[0] + 30, src[1] - 6, steps=3)   # cross the drag threshold
    zone = ui_page.locator(".weekpanel .wg-dropzone")
    expect(zone).to_be_visible()   # the remove strip appears for a bar that is in a lane
    ui_page.mouse.move(*_center(zone), steps=6)
    ui_page.mouse.up()
    until(lambda: _members(ui, g["id"]) == ["gm2"], what="leave")


def test_drag_onto_another_lanes_row_joins(ui, ui_page, make_routine):
    g = _chained_lane(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routines")
    solo_bar = ui_page.locator(".weekpanel a[href='#/routine/uir'] .wg-bar").first
    expect(solo_bar).to_be_visible()

    # the lane row's y from its row; the drop x must be INSIDE the visible strip (the row
    # rect spans all seven laid-out days, so its center x sits scrolled out of view)
    _scroll_strip_to(ui_page, solo_bar)
    chained_bar = ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").first
    row_y = _center(chained_bar)[1]
    sc = ui_page.locator(".weekpanel .wg-scroll").bounding_box()
    _drag(ui_page, _center(solo_bar), (sc["x"] + sc["width"] / 2, row_y))
    until(lambda: _members(ui, g["id"]) == ["gm1", "gm2", "uir"], what="join")


def test_drag_along_own_row_reschedules(ui, ui_page, make_routine):
    """A horizontal drag on the bar of a routine in NO lane re-times its cron: one day-width
    to the right moves the weekly fixture cron (Mon 07:00) to Tuesday, same cadence."""
    import yaml

    ui_page.goto(f"{ui.url}/#/routines")
    bar = ui_page.locator(".weekpanel a[href='#/routine/uir'] .wg-bar").first
    expect(bar).to_be_visible()

    # a weekly fire can sit days away — bring it to the viewport's left edge so the +1 day
    # drop (half the visible two-day strip) still lands inside the window
    _scroll_strip_to(ui_page, bar)
    day_w = ui_page.locator(".weekpanel svg.wg").bounding_box()["width"] / 7
    src = _center(bar)
    _drag(ui_page, src, (src[0] + day_w, src[1]))

    def cron():
        cfg = yaml.safe_load((ui.routines / "uir" / "routine.yaml").read_text())
        return (cfg.get("schedule") or {}).get("cron", "")

    until(lambda: cron().split()[-1:] == ["2"], what="reschedule")
    fields = cron().split()
    assert fields[2] == "*" and fields[3] == "*" and int(fields[1]) in (6, 7), \
        f"weekly cadence should survive the drag: {cron()!r}"


def test_a_lane_name_is_readable_and_its_column_has_one_definition(ui, ui_page, make_routine):
    """The strip answers "what fires today", and the name column answers "whose". At a fixed
    120px the fleet's lane names all truncated to their shared prefix — "⛓ Instance · Ni…",
    "⛓ Instance · Tr…", "⛓ Professional …" twice — so four of eleven rows said the same thing.

    The width now has ONE definition (`--wg-names-w` on `.weekgrid`), read by weekgrid.js to lay
    the SVG out beside the column: the JS constant it used to mirror carried a comment asking
    the next person to keep the two equal, which is the shape that drifts.
    """
    from rsched import lanes

    make_routine(slug="uir3")
    lanes.create(ui.routines, name="Professional · Weekly review",
                 on_failure="stop", members=[{"slug": "uir3"}])
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible()

    names = ui_page.locator(".weekpanel .wg-names")
    declared = ui_page.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('.weekgrid'))"
        ".getPropertyValue('--wg-names-w'))")
    assert declared == 200, f"--wg-names-w is {declared}, not the desktop width"
    assert abs((names.bounding_box() or {}).get("width", 0) - declared) < 1

    # the label reads far enough to tell two "Professional ·" lanes apart
    label = ui_page.locator(".weekpanel .wg-lane-label", has_text="Professional").first
    shown = label.evaluate(
        "e => { const r = document.createRange(); r.selectNodeContents(e);"
        " return r.getBoundingClientRect().width; }")
    assert shown > 150, f"the lane name is clipped to {shown:.0f}px"
