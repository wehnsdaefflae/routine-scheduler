"""Resizable + hideable sidebars (operator request 2026-09-04) in the REAL console.

The main navigation rail is the first of four sidebars sharing static/resizable.js: a grip on the
rail's inner border resizes it on drag (writing --rail-w-set, persisted) and hides/shows it on a
click (toggling .sb-hidden-rail, persisted). The whole feature is scoped to the WIDE layout — the
grip is display:none at ≤1180 and the dragged width lives in --rail-w-set, which only the wide
layout reads — so the narrow icon rail and the mobile bottom bar are untouched.
"""

from __future__ import annotations

from playwright.sync_api import expect


def _rail_var(ui_page, name: str) -> str:
    return ui_page.evaluate(
        f"() => getComputedStyle(document.documentElement).getPropertyValue('{name}').trim()")


def test_the_nav_rail_can_be_dragged_wider_and_the_width_persists(ui, ui_page):
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    grip = ui_page.locator(".sb-grip.rail")
    expect(grip).to_be_visible(timeout=10_000)
    assert _rail_var(ui_page, "--rail-w-set") == "212px"      # the shipped default

    box = grip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    ui_page.mouse.move(cx, cy)
    ui_page.mouse.down()
    ui_page.mouse.move(cx + 70, cy, steps=8)                  # drag the border to the right
    ui_page.mouse.up()

    set_w = int(_rail_var(ui_page, "--rail-w-set").replace("px", ""))
    assert 260 <= set_w <= 300, set_w                         # ~212 + 70, clamped to max 340
    saved = ui_page.evaluate("() => localStorage.getItem('rsched_sb_rail_w')")
    assert saved and abs(int(saved) - set_w) <= 1, saved      # the drag persisted the width

    # a reload restores the stored width before the rail is used again
    ui_page.reload()
    expect(ui_page.locator(".sb-grip.rail")).to_be_visible(timeout=10_000)
    assert _rail_var(ui_page, "--rail-w-set") == f"{saved}px"


def test_clicking_the_grip_hides_then_shows_the_nav_rail(ui, ui_page):
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routines")
    grip = ui_page.locator(".sb-grip.rail")
    expect(grip).to_be_visible(timeout=10_000)
    assert _rail_var(ui_page, "--rail-w") != "0px"            # visible to begin with

    grip.click()                                             # a click (no drag) hides the rail
    assert ui_page.evaluate(
        "() => document.documentElement.classList.contains('sb-hidden-rail')") is True
    assert _rail_var(ui_page, "--rail-w") == "0px"           # the workspace reclaims the width
    assert ui_page.evaluate("() => localStorage.getItem('rsched_sb_rail_hidden')") == "1"
    # the grip stays on-screen as the re-show target even while the rail is hidden
    expect(grip).to_be_visible()

    grip.click()                                            # click again shows it
    assert ui_page.evaluate(
        "() => document.documentElement.classList.contains('sb-hidden-rail')") is False
    assert _rail_var(ui_page, "--rail-w") != "0px"
    assert ui_page.evaluate("() => localStorage.getItem('rsched_sb_rail_hidden')") == "0"
