"""The attachment picker's chips (components/filepicker.js) are removable from the keyboard.

A picked or pasted file shows as a chip that removes it — and the chip was a bare <span> with a
click handler: no role, no tab stop, no key. A keyboard user could attach a file and never take
it off again.
"""

from __future__ import annotations

from playwright.sync_api import expect


def test_an_attachment_chip_is_a_button_the_keyboard_can_press(ui, ui_page, tmp_path):
    first, second = tmp_path / "notes.txt", tmp_path / "plan.txt"
    first.write_text("x", encoding="utf-8")
    second.write_text("y", encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new input[type=file]").set_input_files([str(first), str(second)])

    chip = ui_page.get_by_role("button", name="remove notes.txt")
    expect(chip).to_be_visible()
    chip.focus()
    ui_page.keyboard.press("Enter")
    expect(ui_page.locator(".attach-chip")).to_have_count(1)
    # focus stays on the chips rather than falling to the page
    expect(ui_page.get_by_role("button", name="remove plan.txt")).to_be_focused()
    ui_page.keyboard.press(" ")
    expect(ui_page.locator(".attach-chip")).to_have_count(0)
