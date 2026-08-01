"""Routine detail page: the sections side-TOC (like Settings) and the filesystem-root
directory picker (browse the server FS, pick a real path — no more free-text textarea)."""

import json

from playwright.sync_api import expect


def test_message_the_next_run(ui, ui_page):
    """F233: the routine page carries a "Message the next run" composer — the routine-bound
    home for a note the next run reads at boot (the run page's end-of-run input now only
    continues THAT run). Sending it lands a msg-* file in the routine's inbox."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Message the next run')", timeout=10_000)
    box = ui_page.locator('textarea[data-persist="nextrun-msg-uir"]')
    expect(box).to_be_visible()
    box.fill("re-check the freelance portals after the login fix")
    ui_page.get_by_role("button", name="send to the next run").click()
    expect(_toast(ui_page)).to_contain_text("next run reads it")
    inbox = ui.routine_dir("uir") / "inbox"
    sent = [json.loads(m.read_text(encoding="utf-8")) for m in inbox.glob("msg-*.json")]
    assert any("re-check the freelance portals" in d["text"] for d in sent)


def _toast(page):
    return page.locator("#toast:not([hidden])")


def test_sections_side_toc(ui, ui_page):
    """On a wide viewport the routine page grows a sticky "On this page" rail listing its
    <h2> sections — the same mountToc rail Settings uses (routine.js's recipe file tree is a
    within-section nav and no longer suppresses it)."""
    ui_page.set_viewport_size({"width": 1700, "height": 950})
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Filesystem roots')", timeout=10_000)
    toc = ui_page.locator(".side-toc")
    expect(toc).to_be_visible(timeout=10_000)
    expect(toc).to_contain_text("Filesystem roots")
    expect(toc).to_contain_text("Budgets")

    # D57 phase 2: every config section is now built from the shared settingsSection primitive
    # (heading + .panel + a per-control description) — the SAME primitive the conversation
    # composer uses, so a setting reads and looks identical wherever it appears. The <h2>s the
    # TOC rides are still emitted (asserted above); confirm the primitive's presentation is in
    # use: the sections render inside panels, and the per-control copy renders as a description.
    expect(ui_page.locator(".panel").first).to_be_visible()
    budgets = ui_page.locator("h2:has-text('Budgets')").locator(
        "xpath=following-sibling::div[contains(@class,'panel')][1]")
    expect(budgets.locator(".muted.small").first).to_contain_text("per-run ceilings")


def test_fs_root_directory_picker(ui, ui_page):
    """The fs-roots editor is a real directory picker: the old textarea is gone, and browsing
    to a server directory and selecting it adds it as a root."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Filesystem roots')", timeout=10_000)
    # the free-text "one path per line" textarea is gone
    assert ui_page.locator("textarea[placeholder*='one path per line']").count() == 0

    ui_page.locator("button:has-text('add directory')").first.click()
    picker = ui_page.locator(".dirpicker")
    expect(picker).to_be_visible(timeout=5_000)
    # jump to the fixture home and descend into its routines/ dir, then select it
    picker.locator("input.code").fill(str(ui.tmp))
    picker.locator("button:has-text('go')").click()
    picker.locator(".dp-row", has_text="routines").click()
    picker.get_by_text("select this folder").click()

    expect(picker).to_have_count(0)   # modal closed
    row = ui_page.locator(".root-path")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("routines")
