"""The console's modals are keyboard dialogs (components/dialog.js and its users).

dialog.js replaced every native confirm()/prompt() and promised "keyboard-first (Enter
confirms, Esc or an overlay click cancels)" with focus trapped inside. Three ways it broke that
promise, and one modal that never made it: Enter pressed on the focused CANCEL button ran the
destructive action; one click on the dialog's own text moved focus to the page behind, after
which Escape and the Tab trap went silent; the dialog had no accessible name, so a screen
reader announced an unnamed dialog over a delete. The directory picker (dirpicker.js) had its
own overlay with none of it — no dialog role, no focus on open, no focus back on close.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

RUN = "20260715-150000"


def _open_delete_confirm(ui, ui_page):
    ui.seed_run("uir", RUN, "finished", summary="done")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "notes.md").write_text("# n", encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:{RUN}")
    row = ui_page.locator(".art-row")
    row.hover()
    row.locator(".art-del").click()
    dlg = ui_page.get_by_role("dialog")
    expect(dlg).to_be_visible()
    return dlg, art / "notes.md"


def test_a_confirm_dialog_is_named_by_its_question(ui, ui_page):
    _open_delete_confirm(ui, ui_page)
    expect(ui_page.get_by_role("dialog", name=re.compile(r"Delete artifact notes\.md"))
           ).to_be_visible()


def test_enter_on_the_focused_cancel_button_cancels(ui, ui_page):
    dlg, kept = _open_delete_confirm(ui, ui_page)
    ui_page.keyboard.press("Shift+Tab")                  # from "delete" back to "cancel"
    expect(dlg.get_by_role("button", name="cancel")).to_be_focused()
    ui_page.keyboard.press("Enter")
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
    ui_page.wait_for_timeout(300)
    assert kept.exists(), "Enter on the focused cancel button deleted the file"


def test_escape_still_cancels_after_a_click_on_the_dialogs_own_text(ui, ui_page):
    dlg, kept = _open_delete_confirm(ui, ui_page)
    dlg.locator(".dlg-msg").click()                      # focus leaves the buttons
    ui_page.keyboard.press("Escape")
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
    assert kept.exists()


def test_tab_stays_inside_after_a_click_on_the_dialogs_own_text(ui, ui_page):
    """Backwards from the message — the first thing in the dialog — is the page behind."""
    dlg, _ = _open_delete_confirm(ui, ui_page)
    dlg.locator(".dlg-msg").click()
    ui_page.keyboard.press("Shift+Tab")
    focused_inside = ui_page.evaluate(
        "() => !!document.activeElement.closest('.modal-overlay')")
    assert focused_inside, "Tab left the dialog for the page behind it"


def test_the_folder_picker_is_a_keyboard_dialog(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    add = ui_page.get_by_role("button", name="+ add directory…").first
    add.click()
    dlg = ui_page.get_by_role("dialog", name=re.compile("folder"))
    expect(dlg).to_be_visible()
    expect(dlg.locator("input.code")).to_be_focused()    # typing a path is the way in
    ui_page.keyboard.press("Escape")
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
    expect(add).to_be_focused()                          # back where the reader was


def test_a_folder_row_is_reached_and_opened_by_keyboard(ui, ui_page):
    """The picker's rows were divs with click handlers — inside a dialog whose whole promise
    is the keyboard, a folder could be opened only with a mouse."""
    (ui.tmp / "pick" / "inner").mkdir(parents=True)
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.get_by_role("button", name="+ add directory…").first.click()
    dlg = ui_page.get_by_role("dialog", name=re.compile("folder"))
    dlg.locator("input.code").fill(str(ui.tmp / "pick"))
    ui_page.keyboard.press("Enter")
    folder = dlg.get_by_role("button", name=re.compile("inner"))
    expect(folder).to_be_visible()
    folder.focus()
    ui_page.keyboard.press("Enter")
    expect(dlg.locator("input.code")).to_have_value(re.compile(r"/pick/inner$"))


def test_the_lane_editor_is_a_keyboard_dialog(ui, ui_page):
    """The lane editors had a third, hand-rolled overlay: no accessible name, no focus trap,
    focus never given back. They now ride dialog.js's openModal — but keep their refusal to
    close on a stray scrim click, which would discard a half-made lane."""
    ui_page.goto(f"{ui.url}/#/routines")
    add = ui_page.get_by_role("button", name="＋ new lane")
    add.click()
    dlg = ui_page.get_by_role("dialog", name="New lane")
    expect(dlg).to_be_visible()
    for _ in range(12):                                  # Tab never leaves the editor
        ui_page.keyboard.press("Tab")
        assert ui_page.evaluate("() => !!document.activeElement.closest('[role=dialog]')")
    ui_page.mouse.click(5, 5)                            # the scrim: half-made state stays
    expect(dlg).to_be_visible()
    ui_page.keyboard.press("Escape")
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
    expect(add).to_be_focused()


def test_an_artifacts_delete_is_its_own_keyboard_stop(ui, ui_page):
    """The delete was a span INSIDE the row's open button — nested interactive content no
    keyboard could reach. Two sibling buttons now: Tab from open lands on delete."""
    ui.seed_run("uir", RUN, "finished", summary="done")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "notes.md").write_text("# n", encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:{RUN}")
    ui_page.locator(".art-item").focus()
    ui_page.keyboard.press("Tab")
    delete = ui_page.get_by_role("button", name="delete notes.md")
    expect(delete).to_be_focused()
    ui_page.keyboard.press("Enter")
    expect(ui_page.get_by_role("dialog", name=re.compile(r"Delete artifact notes\.md"))
           ).to_be_visible()
