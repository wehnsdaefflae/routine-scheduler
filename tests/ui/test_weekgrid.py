"""The dashboard "this week" strip: the live green "now" cursor advances on its own between
data refreshes (F230), so an idle dashboard's now-line tracks real time rather than freezing at
the position it had when the page last loaded.
"""

from datetime import datetime

from playwright.sync_api import expect


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
    groups.create(ui.routines, name="Nightly", members=["uir", "uir2"], on_failure="stop")

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
