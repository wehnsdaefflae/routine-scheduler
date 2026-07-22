"""Routine detail page: the sections side-TOC (like Settings) and the filesystem-root
directory picker (browse the server FS, pick a real path — no more free-text textarea)."""

from playwright.sync_api import expect


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
