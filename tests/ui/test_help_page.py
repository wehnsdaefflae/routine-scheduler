"""Help: the guides, the index that reaches them, and what the page says when they are missing.

The live console served `{"guides": [], "api": "api/rsched.html"}` — so Help was one entry
("API reference") over pdoc's submodule index, and every written guide, `docs/getting-started.md`
included, was unreachable from the console. Silence made it read as "this is all there is".

With the source root fixed the other extreme arrives: three dozen guides. They are an index
COLUMN in the server's reading order, not a wrapped wall of chips above the frame.
"""
from __future__ import annotations

import json
import re

from playwright.sync_api import expect


def _index(ui_page, payload: dict) -> None:
    ui_page.route("**/docs/index.json", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload)))


def _many(n: int) -> list[dict]:
    return [{"slug": f"guide-{i}", "title": f"Guide {i}"} for i in range(n)]


def test_an_empty_guide_set_says_so_and_where_to_look(ui, ui_page):
    _index(ui_page, {"version": "0.0.0", "guides": [], "api": "api/rsched.html"})
    ui_page.goto(f"{ui.url}/#/help")
    note = ui_page.locator("[data-no-guides]")
    expect(note).to_be_visible()
    expect(note).to_contain_text("No written guides are published")
    # it names the control that settles it, rather than leaving the reader to guess
    expect(note.get_by_role("link", name="Settings → Source")).to_have_attribute(
        "href", "#/settings?section=source")
    # and the API reference is still served — the note explains the page, it does not replace it
    expect(ui_page.locator(".help-entry", has_text="API reference")).to_be_visible()


def test_guides_lead_and_no_note_is_shown_when_they_exist(ui, ui_page):
    _index(ui_page, {"version": "0.0.0", "api": "api/rsched.html", "guides": [
        {"slug": "getting-started", "title": "Getting started"},
        {"slug": "authoring", "title": "Authoring"}]})
    ui_page.goto(f"{ui.url}/#/help")
    entries = ui_page.locator(".help-entry")
    expect(entries.first).to_have_text("Getting started")      # the server's reading order
    expect(entries.last).to_have_text("API reference")         # generated, and last
    expect(ui_page.locator("[data-no-guides]")).to_have_count(0)
    # the first guide opens by default, and the frame takes the console's theme with it
    expect(ui_page.locator(".help-frame")).to_have_attribute(
        "src", re.compile(r"^/docs/guides/getting-started\.html\?theme=\w+$"))


def test_three_dozen_guides_stay_an_index_beside_the_frame(ui, ui_page):
    """As chips they wrapped into a five-line wall and pushed the reading frame off the first
    screen. A column indexes them all and the frame keeps the top of the page."""
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    _index(ui_page, {"version": "0.0.0", "api": "api/rsched.html", "guides": _many(37)})
    ui_page.goto(f"{ui.url}/#/help")
    expect(ui_page.locator(".help-entry")).to_have_count(38)   # every guide + the reference
    frame = ui_page.locator(".help-frame")
    expect(frame).to_be_in_viewport()
    index_box = ui_page.locator(".help-index").bounding_box()
    frame_box = frame.bounding_box()
    assert index_box["x"] + index_box["width"] <= frame_box["x"] + 1, (
        "the index is above the frame, not beside it")
    assert index_box["height"] < 800, (
        f"the index is {index_box['height']:.0f}px tall — it should scroll inside its column")


def test_opening_a_guide_lights_it_and_loads_it(ui, ui_page):
    _index(ui_page, {"version": "0.0.0", "api": "api/rsched.html", "guides": _many(6)})
    ui_page.goto(f"{ui.url}/#/help")
    ui_page.locator(".help-entry", has_text="Guide 4").click()
    ui_page.wait_for_url(f"{ui.url}/#/help/guide-4")
    expect(ui_page.locator(".help-entry.on")).to_have_text("Guide 4")
    expect(ui_page.locator(".help-frame")).to_have_attribute(
        "src", re.compile(r"^/docs/guides/guide-4\.html\?theme=\w+$"))
