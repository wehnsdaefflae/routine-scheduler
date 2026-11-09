"""The directory picker's opening listing must not overwrite what the reader has typed.

F853. `dirpicker.js` focuses the path field on open — typing a path IS the way in, pinned by
`test_the_folder_picker_is_a_keyboard_dialog`. But the opening listing is still in flight while
those first keystrokes happen, and its write-back (`pathInput.value = data.path`) had no guard
against them: it replaced the reader's text with the daemon's home directory. The next "go" then
read the field it had just clobbered and browsed the wrong folder.

The existing `latest`/`mine` sequence guard does not cover this. It discards a SUPERSEDED
listing — one whose own `load()` has been overtaken — so it protects the window AFTER "go" is
clicked and not the window between typing and clicking, which is the one a reader is in.

WHY THESE TESTS HOLD THE REQUEST INSTEAD OF RACING IT. The symptom in the wild was a flake:
`test_the_folder_picker_keeps_its_height_whatever_the_folder_holds` was re-run 20 times across
eight release gates — the most re-run node in the suite — and the gate's own serial re-run lost
a release cycle to it on 2026-11-07. Its aria snapshot showed the field holding `/home/mark`:
neither what the test typed nor a prefix of it. A test that waits for a race to happen is the
same flake with a different owner, so the listing is HELD by `page.route` and released at the
exact moment that makes the defect certain.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import until


def _hold_the_first_listing(page):
    """Route /api/fs/list so the FIRST request is held and every later one passes."""
    held: list = []

    def hold_first(route):
        # Never unroute a held request: Playwright releases it on the spot.
        if held:
            route.continue_()
        else:
            held.append(route)

    page.route("**/api/fs/list**", hold_first)
    return held


def test_the_opening_listing_does_not_overwrite_a_typed_path(ui, ui_page):
    """The reader types while the opening listing is in flight; their text must stand."""
    (ui.tmp / "few").mkdir()
    typed = str(ui.tmp / "few")

    ui_page.goto(f"{ui.url}/#/conversations")
    held = _hold_the_first_listing(ui_page)
    ui_page.get_by_role("button", name="+ add directory…").first.click()

    dlg = ui_page.get_by_role("dialog", name=re.compile("folder"))
    expect(dlg).to_be_visible()
    expect(dlg.locator("input.code")).to_be_focused()     # the field invites typing at once
    until(lambda: held, what="the opening /api/fs/list request to be held", page=ui_page)

    dlg.locator("input.code").fill(typed)                 # the reader types into it
    held[0].continue_()                                   # the opening listing lands only now

    # WAIT FOR IT TO LAND FIRST. `expect` is satisfied by its first matching poll, so
    # asserting straight after `continue_()` passes whether or not the write-back clobbers
    # the field — the request has not come back yet. (Measured: that version of this test
    # passed against the unfixed picker.) The listing has landed once the list is painted,
    # which is the same response that would overwrite the field.
    expect(dlg.locator(".dirpicker-list .dp-row").first).to_be_visible()

    # It may paint its own folder in the list — it may not touch the field.
    expect(dlg.locator("input.code")).to_have_value(typed)


def test_go_after_the_opening_listing_lands_browses_what_was_typed(ui, ui_page):
    """The consequence that cost a release cycle: "go" reads the field, so a clobbered field
    browses the wrong folder. With the field intact, the typed path is what resolves."""
    (ui.tmp / "few" / "inner").mkdir(parents=True)
    typed = str(ui.tmp / "few")

    ui_page.goto(f"{ui.url}/#/conversations")
    held = _hold_the_first_listing(ui_page)
    ui_page.get_by_role("button", name="+ add directory…").first.click()

    dlg = ui_page.get_by_role("dialog", name=re.compile("folder"))
    expect(dlg.locator("input.code")).to_be_focused()
    until(lambda: held, what="the opening /api/fs/list request to be held", page=ui_page)

    dlg.locator("input.code").fill(typed)
    held[0].continue_()
    dlg.get_by_role("button", name="go", exact=True).click()

    # the daemon answers with the RESOLVED path, which may sit behind a symlinked /tmp
    expect(dlg.locator("input.code")).to_have_value(re.compile(r"/few$"))
    expect(dlg.get_by_role("button", name=re.compile("inner"))).to_be_visible()


def test_the_picker_still_fills_the_field_when_it_navigates_itself(ui, ui_page):
    """The other direction, which a fix that simply stopped writing the field would break:
    clicking a folder row must still show where you now are. That is how the picker is used
    with a mouse, and `test_a_folder_row_is_reached_and_opened_by_keyboard` pins the keyboard
    path through the same write-back."""
    (ui.tmp / "outer" / "inner").mkdir(parents=True)

    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.get_by_role("button", name="+ add directory…").first.click()
    dlg = ui_page.get_by_role("dialog", name=re.compile("folder"))
    dlg.locator("input.code").fill(str(ui.tmp / "outer"))
    ui_page.keyboard.press("Enter")
    expect(dlg.locator("input.code")).to_have_value(re.compile(r"/outer$"))

    dlg.get_by_role("button", name=re.compile("inner")).click()
    expect(dlg.locator("input.code")).to_have_value(re.compile(r"/outer/inner$"))
