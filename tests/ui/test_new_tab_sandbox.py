"""A file a routine wrote, opened in a NEW TAB, must not run as this console.

Every file the console shows from a run is fetched with the bearer token and handed to the
browser as a blob: URL, and a blob URL carries the ORIGIN of the page that made it. Opened
top-level, a page a routine wrote therefore ran as the console itself: `localStorage` and the
operator token in it, and every API route, were the page's to use. The artifact panel frames an
html artifact in a sandboxed iframe for exactly that reason — the "open" link beside it, and the
run file card's "open in a new tab", handed the same bytes over unframed.

Each test plants a page whose script reports what it could read, opens it the way an operator
would, and asks every frame of the new tab what the script found.
"""

from __future__ import annotations

import json

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from .conftest import TOKEN, until

PROBE = ("<!doctype html><title>probe</title><body><p id=out>pending</p><script>"
         "let v; try { v = localStorage.getItem('rsched_token') || 'no token'; }"
         " catch (e) { v = 'blocked'; }"
         "document.getElementById('out').textContent = v;</script>")


def _what_the_page_read(tab) -> list[str]:
    """The probe's verdict from whichever frame of the tab it ran in."""
    found: list[str] = []

    def ran() -> bool:
        found.clear()
        for frame in tab.frames:
            try:
                text = frame.evaluate("() => document.getElementById('out')?.textContent || ''")
            except PlaywrightError:   # a frame mid-navigation has nothing to say yet
                continue
            if text and text != "pending":
                found.append(text)
        return bool(found)

    until(ran, what="the planted page's script", page=tab)
    return found


def test_an_html_artifact_opened_in_a_new_tab_runs_sandboxed(ui, ui_page):
    ui.seed_run("uir", "20260715-150000", "finished", summary="done")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "probe.html").write_text(PROBE, encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-150000")
    ui_page.locator(".art-item", has_text="probe.html").click()
    opener = ui_page.locator(".art-viewer-head a", has_text="open")
    expect(opener).to_be_visible()

    with ui_page.expect_popup() as popup:
        opener.click()
    found = _what_the_page_read(popup.value)

    assert TOKEN not in found, "a routine's page read the operator token from a new tab"
    assert found == ["blocked"], found     # it RAN — scripts work, the origin is opaque


def test_a_page_the_run_wrote_opened_from_the_files_card_runs_sandboxed(ui, ui_page):
    run_dir = ui.seed_run("uir", "20260715-140000", "finished", summary="done")
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "observation", "turn": 1, "payload": {
            "kind": "write_file", "path": "artifacts/probe.html", "bytes": 42}}) + "\n")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "probe.html").write_text(PROBE, encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-140000")
    row = ui_page.locator(".file-row", has_text="probe.html")
    expect(row).to_be_visible()

    with ui_page.expect_popup() as popup:
        row.locator('.file-act[title="open in a new tab"]').click()
    found = _what_the_page_read(popup.value)

    assert TOKEN not in found, "a page the run wrote read the operator token from a new tab"
    assert found == ["blocked"], found


def test_a_passive_file_still_opens_as_itself(ui, ui_page):
    """The frame is for what can carry script. Plain text opens as itself — no wrapper page
    between the reader and the file."""
    ui.seed_run("uir", "20260715-150000", "finished", summary="done")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "notes.txt").write_text("just words", encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-150000")
    ui_page.locator(".art-item", has_text="notes.txt").click()
    opener = ui_page.locator(".art-viewer-head a", has_text="open")
    expect(opener).to_be_visible()

    with ui_page.expect_popup() as popup:
        opener.click()
    tab = popup.value
    tab.wait_for_load_state()
    assert len(tab.frames) == 1, "a text file was wrapped in a frame it does not need"
    expect(tab.locator("body")).to_contain_text("just words")
