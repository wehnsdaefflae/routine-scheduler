"""The Library's curated consequence reminders: the one lever the page owns over them is REMOVAL.

An approval decides what gets into the curated store; without this button nothing could take
an entry out again short of editing the library repo by hand. The button posted its DELETE to
`/library/reminders/<id>` — a path outside the `/api` mount, which only ever answers 404 — and
awaited it with no catch, so the confirm dialog closed onto nothing: no toast, no change, the
entry still listed.
"""

from __future__ import annotations

from playwright.sync_api import expect

from rsched import reminders as store
from rsched.reminders import Reminder

from .conftest import until

RID = "rem-20260905-1"


def _seed_reminder(ui) -> None:
    store.write_global(store.reminders_home(ui.server_cfg.libraries_home),
                       Reminder(id=RID, regex="^util:fs-ops mv ",
                                description="mv overwrites silently", scope="global",
                                created_run="r:1", stats=store.blank_stats(),
                                reach="universal"))


def test_removing_a_curated_reminder_takes_it_out_of_the_store(ui, ui_page):
    _seed_reminder(ui)
    path = store.global_path(store.reminders_home(ui.server_cfg.libraries_home), RID)
    assert path.exists()
    ui_page.goto(f"{ui.url}/#/library")
    row = ui_page.locator("tr", has=ui_page.locator("code", has_text=RID))
    expect(row).to_be_visible()

    row.get_by_role("button", name="remove").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="delete", exact=True).click()

    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text(f"removed {RID}")
    until(lambda: not path.exists(), what="the curated reminder file to go")
    expect(ui_page.locator("code", has_text=RID)).to_have_count(0)


def test_a_refused_removal_says_why(ui, ui_page):
    """A failure is the error toast, never silence: the click must not end in an unhandled
    rejection the operator cannot see."""
    _seed_reminder(ui)
    ui_page.route(f"**/api/library/reminders/{RID}", lambda route: route.fulfill(
        status=409, content_type="application/json",
        body='{"detail": "the library repo is locked"}'))
    ui_page.goto(f"{ui.url}/#/library")
    row = ui_page.locator("tr", has=ui_page.locator("code", has_text=RID))
    row.get_by_role("button", name="remove").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="delete", exact=True).click()

    toast = ui_page.locator("#toast.err:not([hidden])")
    expect(toast).to_contain_text("the library repo is locked")
    expect(row).to_be_visible()


def test_a_row_whose_document_cannot_be_read_says_why(ui, ui_page):
    """Opening any catalogue row is a fetch, and it can fail (a doc deleted from another tab,
    a daemon mid-restart). The deep link always caught that failure; a click on the row did
    not, so the same failure was an error toast on one path and silence on the other."""
    ui_page.route("**/api/library/rules/ask-policy", lambda route: route.fulfill(
        status=404, content_type="application/json", body='{"detail": "no rule \'ask-policy\'"}'))
    ui_page.goto(f"{ui.url}/#/library")
    ui_page.get_by_role("link", name="ask-policy", exact=True).click()
    expect(ui_page.locator("#toast.err:not([hidden])")).to_contain_text("no rule")
