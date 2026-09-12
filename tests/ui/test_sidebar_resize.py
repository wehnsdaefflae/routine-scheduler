"""Resizable + hideable sidebars (operator request 2026-09-04) in the REAL console.

Four sidebars share static/resizable.js: the main navigation rail, the routine page's recipe
file-tree column, and the run/conversation views' LEFT and RIGHT rails — the last two grid
columns whose widths ARE the dragged properties, at every width from 760px up (0.335.0). A grip on each one's
inner border resizes it on drag (writing that surface's `*-set` custom property, persisted) and
hides/shows it on a click (toggling that surface's hidden class, persisted).

The invariant every one of them rests on: the module writes ONLY a custom property and a class,
never inline geometry, so each stylesheet's responsive collapse stays authoritative. Two
consequences are tested here per surface, because both are silent when broken — the grip is
`display: none` in the narrow layout, and a sidebar hidden on a wide screen must come BACK there,
since with no grip to click there would be no way to recover it.
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


# ---- surface 2: the routine page's recipe file-tree column --------------------------------

def _root_var(ui_page, name: str) -> str:
    return ui_page.evaluate(
        f"() => getComputedStyle(document.documentElement).getPropertyValue('{name}').trim()")


def test_the_recipe_file_tree_resizes_hides_and_leaves_the_gutter_alone(ui, ui_page):
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    grip = ui_page.locator(".sb-grip.pagenav")
    expect(grip).to_be_visible(timeout=10_000)
    navcol = ui_page.locator(".recipe-navcol")
    expect(navcol).to_be_visible()
    assert _root_var(ui_page, "--pagenav-w") == "236px"        # the shipped default

    # the grip's negative margins give back the two extra flex gaps it would otherwise add,
    # so the column's right edge and the editor's left edge stay 14px apart
    gutter = ui_page.evaluate(
        "() => Math.round(document.querySelector('.recipe-editorcol').getBoundingClientRect().left"
        " - document.querySelector('.recipe-navcol').getBoundingClientRect().right)")
    assert gutter == 14, gutter

    grip.scroll_into_view_if_needed()      # the recipe panel sits well below the fold
    box = grip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    ui_page.mouse.move(cx, cy)
    ui_page.mouse.down()
    ui_page.mouse.move(cx + 60, cy, steps=8)
    ui_page.mouse.up()
    set_w = int(_root_var(ui_page, "--pagenav-w-set").replace("px", ""))
    assert 280 <= set_w <= 310, set_w                          # ~236 + 60
    assert int(ui_page.evaluate("() => localStorage.getItem('rsched_sb_pagenav_w')")) == set_w

    grip.click()                                               # a click hides the column
    expect(navcol).to_be_hidden()
    expect(grip).to_be_visible()                               # ...and stays the re-show target
    assert ui_page.evaluate("() => localStorage.getItem('rsched_sb_pagenav_hidden')") == "1"

    # narrow: the tree stacks above the editor, so it must reappear — there is no grip there
    ui_page.set_viewport_size({"width": 700, "height": 900})
    expect(grip).to_be_hidden()
    expect(navcol).to_be_visible()

    ui_page.set_viewport_size({"width": 1400, "height": 900})
    grip.click()                                               # click again shows it
    expect(navcol).to_be_visible()


# ---- surfaces 3 + 4: the run / conversation side rails ------------------------------------

def _open_conversation(ui, ui_page):
    """A conversation of its own: on the LIST subpage the right rail is `[hidden]`, so both
    rails only exist together once one is open."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Resize my rails.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")


def test_both_conversation_rails_resize_and_hide_independently(ui, ui_page):
    ui_page.set_viewport_size({"width": 1500, "height": 900})   # the grid mode (1100-1899)
    _open_conversation(ui, ui_page)
    left, right = ui_page.locator(".sb-grip.runrail-l"), ui_page.locator(".sb-grip.runrail-r")
    expect(left).to_be_visible(timeout=10_000)
    assert _root_var(ui_page, "--runrail-l-w") == "218px"       # the grid-mode defaults
    assert _root_var(ui_page, "--runrail-r-w") == "280px"

    box = left.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    ui_page.mouse.move(cx, cy)
    ui_page.mouse.down()
    ui_page.mouse.move(cx + 50, cy, steps=8)
    ui_page.mouse.up()
    l_w = int(_root_var(ui_page, "--runrail-l-w").replace("px", ""))
    assert 255 <= l_w <= 285, l_w
    assert _root_var(ui_page, "--runrail-r-w") == "280px"       # the other rail is untouched

    left.click()                                                # hide the left rail only
    assert _root_var(ui_page, "--runrail-l-w") == "0px"
    assert _root_var(ui_page, "--runrail-r-w") == "280px"
    expect(left).to_be_visible()                                # collapses AROUND its grip
    expect(ui_page.locator(".run-rail.left > summary")).to_be_hidden()

    # the middle tier (760-1099): the RIGHT rail keeps its column and its grip, the
    # conversation index spans the row above the chat with no grip — and the left rail
    # hidden while wide must come back there, since there is nothing to click to recover it
    ui_page.set_viewport_size({"width": 900, "height": 900})
    expect(left).to_be_hidden()
    expect(right).to_be_visible()
    expect(ui_page.locator(".run-rail.left")).to_be_visible()
    # the phone tier: everything stacks, no grips at all
    ui_page.set_viewport_size({"width": 700, "height": 900})
    expect(right).to_be_hidden()
    expect(ui_page.locator(".run-rail.left")).to_be_visible()


def test_a_dragged_rail_width_drives_the_content_column_at_every_wide_width(ui, ui_page):
    """ONE layout above 760px (0.335.0, D125): the view is a grid whose column widths ARE the
    rail width properties, so a dragged width follows the rail to any width and the CONTENT
    column takes exactly what the rails leave — the point the old fixed-margin mode at ≥1900
    missed, where dragging a rail moved nothing but the rail beside a fixed 1240px column."""
    ui_page.set_viewport_size({"width": 1500, "height": 900})
    _open_conversation(ui, ui_page)
    grip = ui_page.locator(".sb-grip.runrail-r")
    expect(grip).to_be_visible(timeout=10_000)
    ui_page.evaluate("() => localStorage.setItem('rsched_sb_runrail-right_w', '300')")
    ui_page.reload()
    expect(ui_page.locator(".sb-grip.runrail-r")).to_be_visible(timeout=10_000)
    assert _root_var(ui_page, "--runrail-r-w") == "300px"

    def widths():
        return ui_page.evaluate(
            "() => [document.querySelector('.conv-view > .run-rail:not(.left)'),"
            " document.querySelector('.conv-main')].map((e) => Math.round(e.getBoundingClientRect().width))")

    for vw in (1960, 2400):                                    # the widest tier is the same grid
        ui_page.set_viewport_size({"width": vw, "height": 900})
        assert _root_var(ui_page, "--runrail-r-w") == "300px"
        rail_w, main_w = widths()
        assert rail_w == 300, (vw, rail_w)
        # the content uses the space: the viewport minus the nav rail, both side rails, the
        # gaps and the page padding — no centred 1240px column in the way any more
        assert main_w > vw - 900, (vw, main_w)
    # hiding the rail hands its column to the content
    ui_page.locator(".sb-grip.runrail-r").click()
    assert _root_var(ui_page, "--runrail-r-w") == "0px"
    _, main_after = widths()
    assert main_after >= main_w + 290, (main_w, main_after)


def test_the_run_view_rail_is_a_resizable_column_at_every_wide_width(ui, ui_page):
    """The run page carries the same rail contract as the conversation page (it used to be a
    stacked block at every width under 1900): a column beside the transcript from 760px with
    its grip on the shared border — drag resizes it, click hides it and the transcript widens."""
    ui.seed_run("uir", "20260715-140000", "finished", summary="done")
    ui_page.set_viewport_size({"width": 1280, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-140000")
    grip = ui_page.locator(".run-view > .sb-grip.runrail-r")
    expect(grip).to_be_visible(timeout=10_000)
    assert _root_var(ui_page, "--runrail-r-w") == "280px"

    def main_width():
        return ui_page.evaluate(
            "() => Math.round(document.querySelector('.run-main').getBoundingClientRect().width)")

    before = main_width()
    box = grip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + 200
    ui_page.mouse.move(cx, cy)
    ui_page.mouse.down()
    ui_page.mouse.move(cx - 40, cy, steps=8)                   # a left rail edge: drag left = wider
    ui_page.mouse.up()
    w = int(_root_var(ui_page, "--runrail-r-w").replace("px", ""))
    assert 305 <= w <= 330, w
    assert main_width() < before                               # the transcript gave the width up

    ui_page.locator(".run-view > .sb-grip.runrail-r").click()  # hide → the transcript widens
    assert _root_var(ui_page, "--runrail-r-w") == "0px"
    expect(ui_page.locator(".run-view > .run-rail")).to_be_hidden()
    assert main_width() > before
    ui_page.locator(".run-view > .sb-grip.runrail-r").click()  # and comes back
    expect(ui_page.locator(".run-view > .run-rail")).to_be_visible()

    ui_page.set_viewport_size({"width": 900, "height": 900})   # still a column mid-width
    expect(ui_page.locator(".run-view > .sb-grip.runrail-r")).to_be_visible()
    ui_page.set_viewport_size({"width": 700, "height": 900})   # stacked on a phone, no grip
    expect(ui_page.locator(".run-view > .sb-grip.runrail-r")).to_be_hidden()
    expect(ui_page.locator(".run-view > .run-rail")).to_be_visible()
