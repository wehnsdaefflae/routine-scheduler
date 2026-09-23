"""The global header search box: instance-wide full text from the topbar — results
drop down grouped by routine, snippets highlight the match, a click deep-links into
the run view, and "/" focuses the box from anywhere.
"""

import json

from playwright.sync_api import expect


def _say_event(run_dir, say, *, turn=1, phase=""):
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-07-15T12:00:01+00:00", "type": "assistant_action",
                             "turn": turn, "phase": phase,
                             "payload": {"say": say, "kind": "util", "name": "x"}}) + "\n")


def test_search_finds_run_and_navigates(ui, ui_page):
    run = ui.seed_run("uir", "20260714-100000", "finished", summary="all done")
    _say_event(run, "zebra migration telemetry captured", phase="gather")
    ui_page.goto(f"{ui.url}/#/routines")
    box = ui_page.locator("#global-search input")
    box.click()
    box.fill("zebra")
    hit = ui_page.locator(".gs-pop .gs-hit").first
    expect(hit).to_be_visible()
    expect(hit).to_contain_text("zebra")
    expect(hit.locator("mark")).to_contain_text("zebra")          # highlighted match
    expect(ui_page.locator(".gs-pop .gs-group")).to_contain_text("uir")  # grouped by routine
    hit.click()
    ui_page.wait_for_url(f"{ui.url}/#/run/uir:20260714-100000")
    expect(ui_page.locator(".gs-pop")).to_be_hidden()             # dropdown closed on navigate


def test_search_no_matches_and_shortcut_focus(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("h1:has-text('Routines')")
    ui_page.keyboard.press("/")                                   # focuses from anywhere
    box = ui_page.locator("#global-search input")
    expect(box).to_be_focused()
    box.fill("xyzzy-nothing-matches-this")
    expect(ui_page.locator(".gs-pop .gs-empty")).to_contain_text("no matches")
    ui_page.keyboard.press("Escape")
    expect(ui_page.locator(".gs-pop")).to_be_hidden()


def test_the_shortcut_is_visible_in_the_box_it_opens(ui, ui_page):
    """The one control that reaches any run, decision or note in a keystroke advertised that
    keystroke only in a hover tooltip. The key rides the box, steps aside while the box is in
    use, and is absent on a phone, where there is no keyboard to advertise."""
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    key = ui_page.locator(".gsearch .gs-key")
    expect(key).to_be_visible()
    expect(key).to_have_text("/")
    ui_page.locator("#global-search input").click()
    expect(key).to_be_hidden()                       # out of the way of what you type

    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.reload()
    ui_page.wait_for_selector("#global-search input")
    expect(ui_page.locator(".gsearch .gs-key")).to_be_hidden()
