"""Groups page (D53 Phase A): the CRUD surface renders, adds a group with a real routine
member, persists to .control/groups.json, changes the instance default, and deletes. Driven
against the REAL console JS — the ui_page fixture also asserts the page threw no JS error."""

import json

from playwright.sync_api import expect

from rsched import group_runs, groups


def test_groups_page_crud(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/groups")
    ui_page.wait_for_selector("[data-groups-default]", timeout=10_000)

    # Phase B is live: the page invites firing a group with Run now
    expect(ui_page.locator(".panel")).to_contain_text("Run now")

    # the member picker offers the fixture routine 'uir'
    picker = ui_page.locator("[data-group-members]")
    expect(picker.locator("option")).to_have_count(1)
    expect(picker.locator("option")).to_have_text("Test uir")

    # add a group with 'uir' as a member
    ui_page.locator(".panel input[type=text]").first.fill("Morning")
    picker.select_option("uir")
    ui_page.get_by_role("button", name="add group").click()

    card = ui_page.locator("[data-group]")
    card.wait_for(timeout=10_000)
    expect(card).to_contain_text("Morning")
    expect(card.locator('[data-member="uir"]')).to_contain_text("uir")

    # it persisted to the store
    def stored():
        return groups.load(ui.routines)
    data = stored()
    assert len(data["groups"]) == 1
    gid = data["groups"][0]["id"]
    assert data["groups"][0]["name"] == "Morning"
    assert data["groups"][0]["members"] == ["uir"]
    assert data["groups"][0]["on_failure"] is None      # inherited by default

    # Run now → arms a sequential fire (Phase B): an in-flight chain lands on disk, snapshotting
    # the member list, and the card shows a running-progress line
    card.get_by_role("button", name="Run now").click()
    expect(card.locator("[data-group-progress]")).to_contain_text("running 1/1", timeout=10_000)
    flight = group_runs.read(ui.routines, gid)
    assert flight is not None and flight["members"] == ["uir"] and flight["cursor"] == 0
    # clear the armed chain so the delete-and-empty-store assertions below stay clean
    group_runs.remove(ui.routines, gid)

    # change the instance default → persists
    ui_page.locator("[data-groups-default]").select_option("continue")
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("continue")
    # give the PUT a beat to land, then verify the store
    ui_page.wait_for_timeout(300)
    assert stored()["default_on_failure"] == "continue"

    # delete the group (confirm dialog → confirm)
    card.get_by_role("button", name="delete group").click()
    ui_page.get_by_role("button", name="delete").last.click()
    expect(ui_page.locator("[data-group]")).to_have_count(0)
    assert stored()["groups"] == []

    # the store file is valid JSON with the expected top-level shape
    raw = json.loads(groups.groups_file(ui.routines).read_text(encoding="utf-8"))
    assert set(raw) == {"default_on_failure", "groups"}
    assert gid not in json.dumps(raw)
