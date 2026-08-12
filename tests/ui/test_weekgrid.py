"""The dashboard "this week" strip: the live green "now" cursor advances on its own between
data refreshes (F230); same-group routines merge onto one labelled lane — a SCHEDULED group's
lane chains its members at the GROUP's fires (D71/R313); and bars drag: onto a sibling to
reorder the group, onto another group's lane to join it, onto the remove strip to leave, and
along the own lane to reschedule.
"""

import time
from datetime import datetime

from playwright.sync_api import expect

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


def _poll(check, timeout_s=8.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.15)
    return False


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
    now.first.wait_for(state="attached", timeout=10_000)
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


def test_same_group_routines_share_one_week_row(ui, ui_page, make_routine):
    """F271 (operator ask): routines in the same group are drawn on the SAME week-strip row, in
    the group's member (execution) order — so a group reads as one chain on the timeline rather
    than scattered across separate rows. Two grouped, scheduled routines → ONE wg-row carrying
    both their bars; ungrouped routines keep their own row."""
    from rsched import groups

    # uir already exists (ui fixture); add a second scheduled routine and a solo one.
    make_routine(slug="uir2")
    make_routine(slug="solo")
    # Group uir + uir2 in fire order; 'solo' stays ungrouped.
    groups.create(ui.routines, name="Nightly", on_failure="stop",
                  members=[{"slug": "uir", "split": False}, {"slug": "uir2", "split": False}])

    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible(timeout=10_000)
    rows = ui_page.locator(".weekpanel .wg-row")
    # The two grouped routines collapse into ONE row; solo has its own → 2 rows, not 3.
    expect(rows).to_have_count(2, timeout=10_000)
    # The merged row carries bars linking to BOTH grouped routines.
    group_row = ui_page.locator(".weekpanel .wg-row",
                                has=ui_page.locator("a[href='#/routine/uir2']")).first
    expect(group_row.locator("a[href='#/routine/uir'] .wg-bar")).to_have_count(1)
    expect(group_row.locator("a[href='#/routine/uir2'] .wg-bar")).to_have_count(1)
    # The legend tags the grouped members with their group name.
    legend = ui_page.locator(".weekpanel .wg-legend")
    expect(legend.locator(".wg-leg-group", has_text="Nightly")).to_have_count(2)


def _chained_group(ui, make_routine):
    """Two fresh routines in a SCHEDULED group 'Chained' (daily 09:30 — the members' own
    weekly fixture crons are suppressed, D71); the harness routine 'uir' stays beside it
    as an ungrouped lane."""
    from rsched import groups

    make_routine(slug="gm1")
    make_routine(slug="gm2")
    return groups.create(ui.routines, name="Chained", on_failure="stop", cron="30 9 * * *",
                         members=[{"slug": "gm1", "split": False}, {"slug": "gm2", "split": False}])


def _members(ui, gid):
    from rsched import groups

    g = groups.get(ui.routines, gid)
    return [m["slug"] for m in (g["members"] if g else [])]


def test_scheduled_group_chains_on_one_labelled_lane(ui, ui_page, make_routine):
    """D71/R313: a group WITH a cron owns its members' schedule. The lane draws the GROUP's
    fires — each member once per fire, chained — so both members carry the SAME bar count,
    the daily count, not their own (suppressed) weekly crons' 1-2. Every lane names itself
    at the left edge, and the legend shows the group's schedule as the members' real one."""
    _chained_group(ui, make_routine)

    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel svg.wg")).to_be_visible(timeout=10_000)
    # two lanes: the group's + the solo harness routine's — each labelled
    expect(ui_page.locator(".weekpanel .wg-row")).to_have_count(2, timeout=10_000)
    expect(ui_page.locator(".weekpanel .wg-lane-label.group", has_text="Chained")).to_have_count(1)
    expect(ui_page.locator(".weekpanel .wg-lane-label", has_text="Test uir")).to_have_count(1)
    n1 = ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").count()
    n2 = ui_page.locator(".weekpanel a[href='#/routine/gm2'] .wg-bar").count()
    assert n1 == n2 and 6 <= n1 <= 8, f"expected the daily group fires on both members, got {n1}/{n2}"
    # the members' legend schedule is the GROUP's, not the vestigial member cron
    expect(ui_page.locator(".weekpanel .wg-leg-sched", has_text="09:30").first).to_be_visible()


def test_drag_onto_sibling_reorders_group(ui, ui_page, make_routine):
    g = _chained_group(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".weekpanel a[href='#/routine/gm2'] .wg-bar").first).to_be_visible(timeout=10_000)

    src = _center(ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").first)
    tgt = _center(ui_page.locator(".weekpanel a[href='#/routine/gm2'] .wg-bar").first)
    _drag(ui_page, src, (tgt[0] + 4, tgt[1]))   # right half of the sibling → "after gm2"
    assert _poll(lambda: _members(ui, g["id"]) == ["gm2", "gm1"]), \
        f"reorder did not land: {_members(ui, g['id'])}"


def test_drag_to_remove_strip_leaves_group(ui, ui_page, make_routine):
    g = _chained_group(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routines")
    bar = ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").first
    expect(bar).to_be_visible(timeout=10_000)

    src = _center(bar)
    ui_page.mouse.move(*src)
    ui_page.mouse.down()
    ui_page.mouse.move(src[0] + 30, src[1] - 6, steps=3)   # cross the drag threshold
    zone = ui_page.locator(".weekpanel .wg-dropzone")
    expect(zone).to_be_visible()   # the remove strip appears for a grouped bar
    ui_page.mouse.move(*_center(zone), steps=6)
    ui_page.mouse.up()
    assert _poll(lambda: _members(ui, g["id"]) == ["gm2"]), \
        f"leave did not land: {_members(ui, g['id'])}"


def test_drag_onto_group_lane_joins(ui, ui_page, make_routine):
    g = _chained_group(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/routines")
    solo_bar = ui_page.locator(".weekpanel a[href='#/routine/uir'] .wg-bar").first
    expect(solo_bar).to_be_visible(timeout=10_000)

    # the group lane's y from its row; the drop x must be INSIDE the visible strip (the row
    # rect spans all seven laid-out days, so its center x sits scrolled out of view)
    _scroll_strip_to(ui_page, solo_bar)
    chained_bar = ui_page.locator(".weekpanel a[href='#/routine/gm1'] .wg-bar").first
    row_y = _center(chained_bar)[1]
    sc = ui_page.locator(".weekpanel .wg-scroll").bounding_box()
    _drag(ui_page, _center(solo_bar), (sc["x"] + sc["width"] / 2, row_y))
    assert _poll(lambda: _members(ui, g["id"]) == ["gm1", "gm2", "uir"]), \
        f"join did not land: {_members(ui, g['id'])}"


def test_drag_along_own_lane_reschedules(ui, ui_page, make_routine):
    """A horizontal drag on an ungrouped routine's bar re-times its cron: one day-width to
    the right moves the weekly fixture cron (Mon 07:00) to Tuesday, same cadence."""
    import yaml

    ui_page.goto(f"{ui.url}/#/routines")
    bar = ui_page.locator(".weekpanel a[href='#/routine/uir'] .wg-bar").first
    expect(bar).to_be_visible(timeout=10_000)

    # a weekly fire can sit days away — bring it to the viewport's left edge so the +1 day
    # drop (half the visible two-day strip) still lands inside the window
    _scroll_strip_to(ui_page, bar)
    day_w = ui_page.locator(".weekpanel svg.wg").bounding_box()["width"] / 7
    src = _center(bar)
    _drag(ui_page, src, (src[0] + day_w, src[1]))

    def cron():
        cfg = yaml.safe_load((ui.routines / "uir" / "routine.yaml").read_text())
        return (cfg.get("schedule") or {}).get("cron", "")

    assert _poll(lambda: cron().split()[-1:] == ["2"]), f"reschedule did not land: {cron()!r}"
    fields = cron().split()
    assert fields[2] == "*" and fields[3] == "*" and int(fields[1]) in (6, 7), \
        f"weekly cadence should survive the drag: {cron()!r}"
