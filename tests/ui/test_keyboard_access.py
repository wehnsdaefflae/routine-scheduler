"""A control is reachable and operable from the keyboard, and says what it does.

`util.tagChip` renders the console's filter chips (the Routines state filter, the Messages type
and status chips, the Library's tag filter and its section counts) and every tag editor's "×"
as clickable SPANS — keeping the chip's own look, which a <button> would trade for the
browser's. But a span with a click handler is not focusable and has no role: none of them could
be reached with Tab or pressed with Enter, and a screen reader announced them as plain text.
The same held for a lane row on the Routines table, which expands on a click in its cell. They
now carry the button role, a tab stop and Enter/Space activation (`util.asButton`); a filter
chip says whether it is on, a lane whether it is open.
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect

from .conftest import until
from .helpers import start_conversation


def test_a_filter_chip_toggles_from_the_keyboard(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    chip = ui_page.locator(".filterbar .tag", has_text="failed")
    expect(chip).to_have_attribute("role", "button")
    expect(chip).to_have_attribute("aria-pressed", "false")

    chip.focus()
    ui_page.keyboard.press("Enter")
    expect(chip).to_have_class("tag click on")      # the bar is rebuilt; the locator re-finds it
    expect(chip).to_have_attribute("aria-pressed", "true")
    expect(chip).to_be_focused()                    # …and the keyboard stays where it was
    expect(ui_page.locator("#view")).to_contain_text("Nothing matches this filter")
    ui_page.keyboard.press(" ")
    expect(chip).to_have_attribute("aria-pressed", "false")


def test_a_lane_row_expands_from_the_keyboard(ui, ui_page):
    """The lane row expands on a click anywhere in its cell; its name is the keyboard's way to
    the same toggle — and keeps the focus across the repaint the toggle causes."""
    from rsched import lanes

    lanes.create(ui.routines, name="Morning", on_failure="stop", members=[{"slug": "uir"}])
    ui_page.goto(f"{ui.url}/#/routines")
    toggle = ui_page.locator("tr[data-lane-row] [aria-expanded]")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(ui_page.locator("tr.lane-member")).to_have_count(0)

    toggle.focus()
    ui_page.keyboard.press("Enter")
    expect(ui_page.locator("tr.lane-member")).to_have_count(1)
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(toggle).to_be_focused()
    ui_page.keyboard.press(" ")
    expect(ui_page.locator("tr.lane-member")).to_have_count(0)


def test_a_library_count_jumps_from_the_keyboard(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/library")
    utils = ui_page.locator('[data-count="utils"]')
    expect(utils).to_have_attribute("role", "button")
    heading = ui_page.locator("h2", has_text="Global utils")
    expect(heading).not_to_be_in_viewport()
    utils.focus()
    ui_page.keyboard.press("Enter")
    expect(heading).to_be_in_viewport()


def test_a_tag_is_removed_from_the_keyboard(ui, ui_page):
    _slug, conv_dir = start_conversation(ui, ui_page, "Plan the trip.")

    adder = ui_page.locator(".conv-tagline input")
    adder.fill("alpha")
    adder.press("Enter")
    remove = ui_page.get_by_role("button", name="remove alpha")
    expect(remove).to_be_visible()

    remove.focus()
    ui_page.keyboard.press("Enter")
    expect(remove).to_have_count(0)
    until(lambda: not yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8"))
          .get("tags"), what="the tag removal to land")


def test_the_self_improvement_notice_is_dismissed_by_name(ui, ui_page, make_routine):
    """Its close control was a bare "×" — announced as "times" — beside a title only a mouse
    hover reads."""
    cfg_path = make_routine(slug="self-audit") / "routine.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg.update(tags=["meta"], enabled=False)
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/help")
    banner = ui_page.locator("#meta-banner")
    expect(banner).to_contain_text("Self-improvement is off")
    banner.get_by_role("button", name="dismiss").click()
    expect(banner).to_be_hidden()
