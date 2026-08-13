"""Group management on the Routines page (D80 — the /groups subpage is retired): the group
toolbar creates a group, the group row runs/pauses it, the overlay editor edits members,
split flags (F292), the instance default, and deletes — all persisting to
.control/groups.json. Driven against the REAL console JS — the ui_page fixture also asserts
the page threw no JS error."""

import json

from playwright.sync_api import expect

from rsched import group_runs, groups


def test_routine_page_hero_group_select_assigns_membership(ui, ui_page):
    """Membership from the routine DETAIL page (user order 2026-08-12): the hero's group
    select joins the routine to a scheduling group — same PATCH the Routines page's group
    surface uses — and shows the current membership."""
    groups.create(ui.routines, name="Nightly", members=[])
    ui_page.goto(f"{ui.url}/#/routine/uir")
    sel = ui_page.locator(".hero-group-sel")
    expect(sel).to_be_visible(timeout=10_000)
    expect(sel.locator("option")).to_have_count(2)          # none + Nightly
    sel.select_option(label="Nightly")
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text(
        "joined", timeout=10_000)
    data = groups.load(ui.routines)
    assert data["groups"][0]["members"] == [{"slug": "uir", "split": False}]


def test_routines_page_group_crud(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("[data-group-new]", timeout=10_000)

    # create: the toolbar's "+ new group" opens the overlay form
    ui_page.locator("[data-group-new]").click()
    ui_page.locator("[data-group-new-name]").fill("Morning")
    picker = ui_page.locator("[data-group-members]")
    expect(picker.locator("option")).to_have_count(1)
    expect(picker.locator("option")).to_have_text("Test uir")
    picker.select_option("uir")
    ui_page.get_by_role("button", name="add group").click()

    row = ui_page.locator("tr[data-group-row]")
    row.wait_for(timeout=10_000)
    expect(row).to_contain_text("Morning")

    # it persisted to the store, as member RECORDS (F292)
    def stored():
        return groups.load(ui.routines)
    data = stored()
    assert len(data["groups"]) == 1
    gid = data["groups"][0]["id"]
    assert data["groups"][0]["name"] == "Morning"
    assert data["groups"][0]["members"] == [{"slug": "uir", "split": False}]
    assert data["groups"][0]["on_failure"] is None      # inherited by default

    # the editor toggles the member's split flag (F292: fires once per two-phase pass)
    row.locator("[data-group-edit]").click()
    editor = ui_page.locator(f'[data-group="{gid}"]')
    editor.wait_for(timeout=10_000)
    expect(editor.locator('[data-member="uir"]')).to_contain_text("uir")
    editor.locator('[data-member-split="uir"]').check()
    ui_page.wait_for_timeout(300)   # give the PATCH a beat to land
    assert stored()["groups"][0]["members"] == [{"slug": "uir", "split": True}]
    ui_page.locator("[data-group-editor-close]").click()

    # the expanded group row badges the split member
    ui_page.locator("tr[data-group-row]", has_text="Morning").get_by_text("⛓ Morning").click()
    expect(ui_page.locator("tr.group-member [data-member-split-badge]")).to_be_visible(
        timeout=10_000)

    # Run now → arms a sequential fire; the row shows the ingest-pass progress and the
    # in-flight chain snapshots the member records
    ui_page.locator("[data-group-run]").click()
    expect(ui_page.locator("[data-group-progress]")).to_contain_text(
        "ingest 1/1", timeout=10_000)
    flight = group_runs.read(ui.routines, gid)
    assert flight is not None and flight["cursor"] == 0
    assert flight["phase"] == "ingest"
    assert flight["members"] == [{"slug": "uir", "split": True}]
    # clear the armed chain so the delete-and-empty-store assertions below stay clean
    group_runs.remove(ui.routines, gid)

    # change the instance default → persists
    ui_page.locator("[data-groups-default]").select_option("continue")
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("continue")
    ui_page.wait_for_timeout(300)
    assert stored()["default_on_failure"] == "continue"

    # delete (from the editor; confirm dialog → confirm)
    ui_page.locator("tr[data-group-row] [data-group-edit]").click()
    ui_page.locator(f'[data-group="{gid}"]').wait_for(timeout=10_000)
    ui_page.get_by_role("button", name="delete group").click()
    ui_page.get_by_role("button", name="delete", exact=True).last.click()
    expect(ui_page.locator("tr[data-group-row]")).to_have_count(0, timeout=10_000)
    assert stored()["groups"] == []

    # the store file is valid JSON with the expected top-level shape
    raw = json.loads(groups.groups_file(ui.routines).read_text(encoding="utf-8"))
    assert set(raw) == {"default_on_failure", "groups"}
    assert gid not in json.dumps(raw)


def test_routines_page_group_pause_toggle(ui, ui_page):
    """Whole-group pause on the group row: a SCHEDULED group offers ⏸ pause — clicking
    persists paused=true to the store and shows the badge; resume clears both. An
    unscheduled group shows no toggle (there is no cron to pause; ▶ run now is its only
    fire path)."""
    rec = groups.create(ui.routines, name="Sched", members=[{"slug": "uir", "split": False}],
                        cron="0 7 * * *", tz="UTC")
    plain = groups.create(ui.routines, name="Plain", members=[{"slug": "uir", "split": False}])
    ui_page.goto(f"{ui.url}/#/routines")
    sched_row = ui_page.locator(f'tr[data-group-row="{rec["id"]}"]')
    sched_row.wait_for(timeout=10_000)

    # only the scheduled row offers the toggle
    expect(sched_row.locator("[data-group-pause-toggle]")).to_have_text("⏸ pause")
    expect(ui_page.locator(
        f'tr[data-group-row="{plain["id"]}"] [data-group-pause-toggle]')).to_have_count(0)

    # pause → badge appears, store carries paused=true
    sched_row.locator("[data-group-pause-toggle]").click()
    expect(ui_page.locator(
        f'tr[data-group-row="{rec["id"]}"] [data-group-paused]')).to_contain_text(
        "paused", timeout=10_000)
    assert groups.get(ui.routines, rec["id"])["paused"] is True

    # resume → badge gone, store cleared (the row re-renders, so re-locate)
    ui_page.locator(f'tr[data-group-row="{rec["id"]}"] [data-group-pause-toggle]').click()
    expect(ui_page.locator(
        f'tr[data-group-row="{rec["id"]}"] [data-group-paused]')).to_have_count(
        0, timeout=10_000)
    ui_page.wait_for_timeout(300)   # give the PATCH a beat, as the CRUD test does
    assert groups.get(ui.routines, rec["id"])["paused"] is False


def test_retired_groups_route_falls_back_home(ui, ui_page):
    """D80: #/groups is gone — an old bookmark lands on the router's fallback (the
    Conversations landing), never a broken view."""
    ui_page.goto(f"{ui.url}/#/groups")
    ui_page.wait_for_url(f"{ui.url}/#/", timeout=10_000)


def test_group_editor_shared_config_section(ui, ui_page):
    """D82: the group editor's "Shared config" section is where the block every member
    inherits is edited. Exercises the real panel end to end — it mounts the ROUTINE page's own
    permissions control, and a save lands in .control/groups.json as the group's config."""
    g = groups.create(ui.routines, name="Nightly", members=[{"slug": "uir", "split": False}])
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("tr[data-group-row]", timeout=10_000)
    ui_page.locator("tr[data-group-row] [data-group-edit]").click()
    editor = ui_page.locator(f'[data-group="{g["id"]}"]')
    editor.wait_for(timeout=10_000)

    # the section is present, collapsed, and says what inheritance means
    section = editor.locator("[data-group-config-section]")
    expect(section).to_be_visible(timeout=10_000)
    expect(section).to_contain_text("Shared config")
    section.locator("summary").click()
    panel = editor.locator(f'[data-group-config="{g["id"]}"]')
    expect(panel).to_be_visible(timeout=10_000)
    expect(panel).to_contain_text("Every member inherits this")
    # the blocks that make the shared half editable are all mounted
    for title in ("Permissions & capabilities", "General rules", "Secrets", "Connections",
                  "Filesystem — readable", "Filesystem — writable"):
        expect(panel.locator(f'[data-gcfg-block="{title}"]')).to_be_visible()

    # a save writes the group's config (fs roots are the simplest control to drive headlessly)
    panel.locator("[data-group-fs_read_roots-save]").click()
    ui_page.wait_for_timeout(300)
    assert "config" in groups.load(ui.routines)["groups"][0]
