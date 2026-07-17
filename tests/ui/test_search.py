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
    ui_page.goto(ui.url)
    box = ui_page.locator("#global-search input")
    box.click()
    box.fill("zebra")
    hit = ui_page.locator(".gs-pop .gs-hit").first
    expect(hit).to_be_visible(timeout=10_000)
    expect(hit).to_contain_text("zebra")
    expect(hit.locator("mark")).to_contain_text("zebra")          # highlighted match
    expect(ui_page.locator(".gs-pop .gs-group")).to_contain_text("uir")  # grouped by routine
    hit.click()
    ui_page.wait_for_url(f"{ui.url}/#/run/uir:20260714-100000", timeout=10_000)
    expect(ui_page.locator(".gs-pop")).to_be_hidden()             # dropdown closed on navigate


def test_search_no_matches_and_shortcut_focus(ui, ui_page):
    ui_page.goto(ui.url)
    ui_page.wait_for_selector("h1:has-text('Routines')", timeout=10_000)
    ui_page.keyboard.press("/")                                   # focuses from anywhere
    box = ui_page.locator("#global-search input")
    expect(box).to_be_focused()
    box.fill("xyzzy-nothing-matches-this")
    expect(ui_page.locator(".gs-pop .gs-empty")).to_contain_text("no matches", timeout=10_000)
    ui_page.keyboard.press("Escape")
    expect(ui_page.locator(".gs-pop")).to_be_hidden()
