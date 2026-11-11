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


def _seed_transcript(run, events: list[dict]) -> None:
    """A transcript the replay can rebuild — what the `history`/`observations` click serves."""
    import json

    (run / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_clicking_a_grown_segment_shows_what_was_sent(ui, ui_page):
    """R3341, the operator's own words: "can we click on it to see the actual prompt?"

    `history` is the message list, which IS on disk, so the click opens the real text — replayed
    through the engine's own renderers — and the panel says it is a recording rather than
    implying it. Asserts the opener is PRESENT before asserting what it opens.
    """
    ts = "20260907-140000"
    run = _seed_with_segments(ui, ts, state="finished")
    _seed_transcript(run, [
        {"type": "assistant_action", "turn": 1,
         "payload": {"say": "reading the plan", "kind": "read_file", "path": "state/plan.md"}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "read_file", "path": "state/plan.md", "content": "PLAN BODY HERE",
                     "start_line": 1, "end_line": 1, "total_lines": 1}},
    ])

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    opener = ui_page.locator('[data-prompt-segments] button[data-segment="history"]')
    expect(opener).to_have_count(1)
    opener.click()

    panel = ui_page.locator(".modal-overlay .panel")
    expect(panel).to_have_count(1)
    expect(panel).to_contain_text("what was sent")
    expect(panel).to_contain_text("replayed from this run's transcript")
    row = panel.locator("button", has_text="turn 1")
    expect(row).to_have_count(1)
    row.click()
    expect(panel.locator("pre.doc")).to_contain_text("reading the plan")


def test_clicking_an_always_on_section_shows_the_text_captured_at_boot(ui, ui_page):
    """His option B: the always-on half of the bar is readable too, because the section texts
    are recorded once at boot. The panel says WHICH prompt it is holding.
    """
    from rsched.engine import promptcapture

    ts = "20260907-150000"
    run = _seed_with_segments(ui, ts, state="finished")
    promptcapture.write(run, [("harness", "THE HARNESS CONTRACT, VERBATIM"),
                              ("capabilities", "what this run can use")])

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    opener = ui_page.locator('[data-prompt-segments] button[data-segment="harness"]')
    expect(opener).to_have_count(1)
    opener.click()

    panel = ui_page.locator(".modal-overlay .panel")
    expect(panel).to_have_count(1)
    expect(panel).to_contain_text("this leg's composed section")
    panel.locator("button", has_text="turn 0").click()
    expect(panel.locator("pre.doc")).to_contain_text("THE HARNESS CONTRACT, VERBATIM")


def test_a_section_with_no_capture_says_the_text_was_never_stored(ui, ui_page):
    """Every run recorded before the capture shipped. The refusal is the feature here: a text
    presented as the prompt that is not the prompt is worse than no text.
    """
    ts = "20260907-160000"
    _seed_with_segments(ui, ts, state="finished")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    ui_page.locator('[data-prompt-segments] button[data-segment="digest"]').click()
    panel = ui_page.locator(".modal-overlay .panel")
    expect(panel).to_have_count(1)
    expect(panel).to_contain_text("never stored")
    expect(panel.locator("pre.doc")).to_have_count(0)


def test_leaving_the_run_closes_an_open_segment_panel(ui, ui_page):
    """The panel lives on document.body, so the rail leaving the document does not remove it:
    the bar's own stop() has to, or a reader who navigates away keeps a dialog about a run they
    have left (tests/ui/test_view_teardown.py's rule, for a node outside the rail).
    """
    ts = "20260907-170000"
    _seed_with_segments(ui, ts, state="finished")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    ui_page.locator('[data-prompt-segments] button[data-segment="harness"]').click()
    expect(ui_page.locator(".modal-overlay")).to_have_count(1)
    ui_page.evaluate("location.hash = '#/help'")
    ui_page.wait_for_selector("#view h1")
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
