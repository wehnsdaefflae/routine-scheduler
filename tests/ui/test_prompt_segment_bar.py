"""The run rail's PROMPT BY SEGMENT bar (D270) — what this turn's window is being spent on.

`engine/promptsegments.py` measures the prompt's make-up beside the token-ratio calibration and
`status.json` carries it; `GET /api/runs/<id>` hands it to the page and
`components/promptsegbar.js` draws it as one stacked bar in the rail. The measurement half is
pinned in tests/test_prompt_segments.py — these are the claims only a BROWSER can settle:

- a run with a breakdown shows the bar, one drawn segment per measured part, in prompt order,
  with each part's token count and share readable beside it;
- a run with no breakdown (recorded before the measurement, or one no provider ever reported a
  prompt size for) gains NO rail caption at all — tests/ui/test_finish_line.py pins the caption
  list exactly, and an always-present empty card teaches a reader to skip the one place the
  window is explained;
- the bar's poller dies with the view, like every other poller in the rail.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .helpers import watch_requests

#: In the composer's own prompt order, with `observations` dominant — the shape a long run has.
SEGMENTS = {"harness": 7000, "capabilities": 2400, "digest": 600,
            "history": 3000, "observations": 24000}
TOTAL = sum(SEGMENTS.values())


def _seed_with_segments(ui, ts: str, state: str = "running", segments: dict | None = None):
    """A run whose status.json carries a breakdown — `seed_run` writes the rest of the file."""
    from rsched.paths import atomic_write_json, read_json

    run = ui.seed_run("uir", ts, state, summary="")
    status = read_json(run / "status.json")
    status["prompt_segments"] = SEGMENTS if segments is None else segments
    atomic_write_json(run / "status.json", status)
    return run


def test_a_measured_run_shows_its_prompt_broken_down_by_segment(ui, ui_page):
    ts = "20260907-100000"
    _seed_with_segments(ui, ts)

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")

    bar = ui_page.locator("[data-prompt-segments]")
    expect(bar).to_have_count(1)
    expect(bar).to_be_visible()
    # one rect per measured segment, every one of them actually drawn
    expect(bar.locator("svg rect")).to_have_count(len(SEGMENTS))
    assert bar.get_attribute("data-prompt-segments") == str(len(SEGMENTS))
    # the total, and the part a reader is looking for: where the window went. util.js's
    # fmtNum compacts a token count, so 37000 reads "37.0k" — assert the rendered form.
    assert TOTAL == 37000
    expect(bar).to_contain_text("37.0k estimated tokens, in prompt order")
    expect(bar).to_contain_text("24.0k")          # observations, the dominant segment
    expect(bar).to_contain_text("observations")
    expect(bar).to_contain_text("65%")            # 24000 / 37000
    expect(bar).to_contain_text("harness")
    # and it is a foldable rail section like every other
    expect(ui_page.locator('.run-rail .rail-cap[data-rail="prompt"]')).to_be_visible()


def test_the_segments_are_painted_in_the_series_palette_and_sized_by_share(ui, ui_page):
    """The bar is READ as a bar: a segment's width is its share of the prompt, and the colours
    are base.css's --series tokens, so a theme switch repaints them (components/charts.js'
    convention). A stacked bar whose widths did not track the numbers would mislead silently.
    """
    ts = "20260907-110000"
    _seed_with_segments(ui, ts, segments={"harness": 7500, "observations": 2500})

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    rects = ui_page.locator("[data-prompt-segments] svg rect")
    expect(rects).to_have_count(2)
    widths = [float(rects.nth(i).get_attribute("width")) for i in range(2)]
    assert widths == [75.0, 25.0], widths
    xs = [float(rects.nth(i).get_attribute("x")) for i in range(2)]
    assert xs == [0.0, 75.0], xs           # stacked, not overlaid
    fills = [rects.nth(i).get_attribute("fill") for i in range(2)]
    assert fills == ["var(--series-1)", "var(--series-2)"], fills
    assert [c != "rgb(0, 0, 0)" for c in
            [rects.nth(i).evaluate("r => getComputedStyle(r).fill") for i in range(2)]]


def test_a_run_that_measured_nothing_gains_no_prompt_card(ui, ui_page):
    """The rail's captions are unchanged for every run recorded before the measurement."""
    ts = "20260907-120000"
    ui.seed_run("uir", ts, "finished", summary="done")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    expect(ui_page.locator(".run-rail .rail-cap").first).to_be_visible()
    caps = [c.strip().lower() for c in
            ui_page.locator(".run-rail .rail-cap").all_inner_texts()]
    assert "prompt" not in caps
    assert ui_page.locator("[data-prompt-segments]").count() == 0


def test_leaving_the_run_stops_the_bars_own_poll(ui, ui_page):
    """A rail poller that outlives its view asks for a run nobody is looking at for the rest of
    the browser session (tests/ui/test_view_teardown.py). The bar reads the run detail itself,
    so it owns that risk itself."""
    ts = "20260907-130000"
    _seed_with_segments(ui, ts)

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    expect(ui_page.locator("[data-prompt-segments]")).to_have_count(1)
    ui_page.evaluate("location.hash = '#/help'")
    ui_page.wait_for_selector("#view h1")

    after = watch_requests(ui_page, re.compile("/api/"))
    ui_page.wait_for_timeout(4500)
    leaked = [u for u in after if ts in u]
    assert not leaked, f"the prompt-segment bar polled a run nobody is looking at: {leaked}"
