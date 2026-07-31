"""The dashboard "this week" strip: the live green "now" cursor advances on its own between
data refreshes (F230), so an idle dashboard's now-line tracks real time rather than freezing at
the position it had when the page last loaded.
"""

def test_now_cursor_advances_on_its_own(ui, ui_page):
    """With no data refresh, the wg-now cursor re-positions itself on the component's internal
    timer. Driven by Playwright's fake clock so it is deterministic (no 30s real wait): capture
    the cursor's x, fast-forward two hours, and assert it moved to the right."""
    # Install the fake clock BEFORE any script runs (freezes at the real current time, which the
    # server's fire computation also uses — so the browser's day columns and now-cursor align).
    ui_page.clock.install()
    ui_page.goto(f"{ui.url}/#/")

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
