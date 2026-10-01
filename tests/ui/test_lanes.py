"""Lane management on the Routines page — a lane has no subpage of its own (D80).

A LANE is the temporal axis (docs/lanes-tags.md). The toolbar creates one, the lane row
runs/pauses it, the overlay editor edits members, order, schedule and on-failure; the lot
persists to `.control/lanes.json`. A lane carries no config and no store at all — which is what
`test_moving_a_routine_between_lanes_leaves_its_config_alone` pins, because a record holding
both would make a timing decision silently a permissions decision.

The ROUTINE page's half is here as well: the hero tile that READS this routine's lane without
offering to change it.

Driven against the REAL console JS — the ui_page fixture also asserts the page threw no JS
error."""

import json

import yaml
from playwright.sync_api import expect

from rsched import lane_runs, lanes

from .conftest import TOKEN, until


def _detail(ui, ui_page, slug: str) -> dict:
    """The routine's config as the console reads it."""
    r = ui_page.request.get(f"{ui.url}/api/routines/{slug}",
                            headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.ok, r.status
    return r.json()


def test_routine_page_hero_reports_the_lane_without_offering_to_change_it(ui, ui_page):
    """The hero READS this routine's lane and links to where lanes are edited. A lane orders
    several routines and belongs to no single one of them, so it is instance state the Routines
    page owns; a picker here would sit among controls that are otherwise all this routine's own
    config, which is how a timing decision turns into a permissions change by side effect."""
    lanes.create(ui.routines, name="Nightly", members=[])
    ui_page.goto(f"{ui.url}/#/routine/uir")
    tile = ui_page.locator("[data-hero-lane]")
    expect(tile).to_be_visible()
    expect(tile.locator(".hero-strong")).to_have_text("none")
    expect(tile.locator("select")).to_have_count(0)
    expect(tile.locator("a.hero-link")).to_have_attribute("href", "#/routines")
    assert lanes.load(ui.routines)["lanes"][0]["members"] == []   # reading it joined nothing


def test_hero_lane_tile_names_an_unscheduled_lane(ui, ui_page):
    """F388 (R499/R500): the tile reads MEMBERSHIP from /api/lanes, never the detail payload's
    `lane_managed` flag — that answers a different question ("does a SCHEDULED lane drive this
    routine's fires?", D71) and is null here. Reading it as membership rendered a persisted
    assignment as "none", so the user assigned the lane again and reported data loss."""
    lanes.create(ui.routines, name="Unscheduled", members=[{"slug": "uir"}])
    ui_page.goto(f"{ui.url}/#/routine/uir")
    tile = ui_page.locator("[data-hero-lane]")
    expect(tile.locator(".hero-strong")).to_have_text("Unscheduled")
    expect(tile.locator(".hero-sub")).to_contain_text("its own cron applies")
    expect(tile.locator("a.hero-link")).to_have_attribute("href", "#/routines")


def test_hero_lane_tile_says_a_scheduled_lane_drives_the_fires(ui, ui_page):
    """The same tile for a SCHEDULED lane (D71): the chain fires the members in order, so this
    routine's own cron is suppressed and the sub-line says which of the two is in charge."""
    lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}], cron="0 3 * * *")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    tile = ui_page.locator("[data-hero-lane]")
    expect(tile.locator(".hero-strong")).to_have_text("Nightly")
    expect(tile.locator(".hero-sub")).to_contain_text("fires via the lane's chain")


def test_routines_page_lane_crud(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("[data-lane-new]")

    # create: the toolbar's "+ new lane" opens the overlay form
    ui_page.locator("[data-lane-new]").click()
    ui_page.locator("[data-lane-new-name]").fill("Morning")
    picker = ui_page.locator("[data-lane-members]")
    expect(picker.locator("option")).to_have_count(1)
    expect(picker.locator("option")).to_have_text("Test uir")
    picker.select_option("uir")
    ui_page.get_by_role("button", name="add lane").click()

    row = ui_page.locator("tr[data-lane-row]")
    row.wait_for()
    expect(row).to_contain_text("Morning")

    # it persisted to the store, as member RECORDS
    def stored():
        return lanes.load(ui.routines)
    data = stored()
    assert len(data["lanes"]) == 1
    lane_id = data["lanes"][0]["id"]
    assert data["lanes"][0]["name"] == "Morning"
    assert data["lanes"][0]["members"] == [{"slug": "uir"}]
    assert data["lanes"][0]["on_failure"] is None      # inherited by default

    # the editor opens and lists the member
    row.locator("[data-lane-edit]").click()
    editor = ui_page.locator(f'[data-lane="{lane_id}"]')
    editor.wait_for()
    expect(editor.locator('[data-member="uir"]')).to_contain_text("uir")
    ui_page.locator("[data-lane-editor-close]").click()

    # Run now → arms a sequential fire; the row shows the chain progress and the
    # in-flight chain snapshots the member records
    ui_page.locator("tr[data-lane-row]", has_text="Morning").get_by_text("⛓ Morning").click()
    ui_page.locator("[data-lane-run]").click()
    expect(ui_page.locator("[data-lane-progress]")).to_contain_text(
        "1/1")
    flight = lane_runs.read(ui.routines, lane_id)
    assert flight is not None and flight["cursor"] == 0
    assert flight["members"] == [{"slug": "uir"}]
    # clear the armed chain so the delete-and-empty-store assertions below stay clean
    lane_runs.remove(ui.routines, lane_id)

    # change the instance default → persists (it is set once and lives behind a fold, so the
    # bar above the routine list is "＋ new lane" and nothing else at rest)
    ui_page.locator("[data-lane-defaults] summary").click()
    ui_page.locator("[data-lanes-default]").select_option("continue")
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("continue")
    until(lambda: stored().get("default_on_failure") == "continue", what="the default save")

    # delete (from the editor; confirm dialog → confirm)
    ui_page.locator("tr[data-lane-row] [data-lane-edit]").click()
    ui_page.locator(f'[data-lane="{lane_id}"]').wait_for()
    ui_page.get_by_role("button", name="delete lane").click()
    ui_page.get_by_role("button", name="delete", exact=True).last.click()
    expect(ui_page.locator("tr[data-lane-row]")).to_have_count(0)
    assert stored()["lanes"] == []

    # the store file is valid JSON with the expected top-level shape
    raw = json.loads(lanes.lanes_file(ui.routines).read_text(encoding="utf-8"))
    assert set(raw) == {"default_on_failure", "lanes"}
    assert lane_id not in json.dumps(raw)


def test_routines_page_lane_pause_toggle(ui, ui_page, make_routine):
    """Whole-lane pause on the lane row: a SCHEDULED lane offers ⏸ pause — clicking persists
    paused=true to the store and shows the badge; resume clears both. An unscheduled lane shows
    no toggle (there is no cron to pause; ▶ run now is its only fire path).

    The two lanes hold DIFFERENT routines: a routine belongs to at most one lane and the store
    enforces it, so the same member in both would be refused before the page ever renders."""
    make_routine(slug="uir2")
    rec = lanes.create(ui.routines, name="Sched", members=[{"slug": "uir"}],
                       cron="0 7 * * *", tz="UTC")
    plain = lanes.create(ui.routines, name="Plain", members=[{"slug": "uir2"}])
    ui_page.goto(f"{ui.url}/#/routines")
    sched_row = ui_page.locator(f'tr[data-lane-row="{rec["id"]}"]')
    sched_row.wait_for()

    # only the scheduled row offers the toggle
    expect(sched_row.locator("[data-lane-pause-toggle]")).to_have_text("⏸ pause")
    expect(ui_page.locator(
        f'tr[data-lane-row="{plain["id"]}"] [data-lane-pause-toggle]')).to_have_count(0)

    # pause → badge appears, store carries paused=true
    sched_row.locator("[data-lane-pause-toggle]").click()
    expect(ui_page.locator(
        f'tr[data-lane-row="{rec["id"]}"] [data-lane-paused]')).to_contain_text(
        "paused")
    assert lanes.get(ui.routines, rec["id"])["paused"] is True

    # resume → badge gone, store cleared (the row re-renders, so re-locate)
    ui_page.locator(f'tr[data-lane-row="{rec["id"]}"] [data-lane-pause-toggle]').click()
    expect(ui_page.locator(
        f'tr[data-lane-row="{rec["id"]}"] [data-lane-paused]')).to_have_count(
        0)
    until(lambda: lanes.get(ui.routines, rec["id"])["paused"] is False, what="the resume")


def test_no_lane_or_group_subpage_exists(ui, ui_page):
    """Lanes are managed on the Routines page, so they have no subpage: #/lanes hits the
    router's fallback (the Conversations landing) rather than a broken view. `groups` is named
    here ON PURPOSE: it is a DEAD route operators still hold bookmarks to (D80), so it has to
    land somewhere real rather than on a view that throws."""
    # a route that never existed, then the dead one a bookmark can still ask for
    for route in ("lanes", "groups"):
        ui_page.goto(f"{ui.url}/#/{route}")
        ui_page.wait_for_url(f"{ui.url}/#/")


def test_moving_a_routine_between_lanes_leaves_its_config_alone(ui, ui_page):
    """The clearest behavioural consequence of the lane owning nothing but timing
    (docs/lanes-tags.md). What a routine may do and reach is its own routine.yaml, so no lane
    edit can reach it. A lane that carried config would make moving a member from one lane to
    another silently change its effective permissions — a timing decision doing the work of a
    permissions decision, with nothing on the page to say so.
    """
    path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg.update({"fs_read_roots": ["/srv/fau"], "rules": []})
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    nightly = lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}])
    weekly = lanes.create(ui.routines, name="Weekly", members=[])

    before = _detail(ui, ui_page, "uir")
    assert before["fs_read_roots"]

    # The move the lane editor's member rows make — a members PATCH per lane and nothing else.
    # Membership is exclusive, so it leaves the one lane before it joins the other.
    for lane_id, members in ((nightly["id"], []), (weekly["id"], [{"slug": "uir"}])):
        r = ui_page.request.patch(f"{ui.url}/api/lanes/{lane_id}",
                                  headers={"Authorization": f"Bearer {TOKEN}"},
                                  data={"members": members})
        assert r.ok, r.text()
    moved = lanes.lane_of(ui.routines, "uir")
    assert moved is not None and moved["name"] == "Weekly"

    after = _detail(ui, ui_page, "uir")
    for key in ("permissions", "capabilities", "fs_read_roots", "fs_write_roots", "rules",
                "grants"):
        assert after[key] == before[key], f"the lane move changed {key}"


def test_lane_editor_offers_no_shared_config(ui, ui_page):
    """The same invariant from the other side: there is nothing IN a lane editor that could
    change a member's config. What a member may do is its own config, so the editor offers
    members, order, schedule and on-failure and nothing else.

    The pin is the editor's POSITIVE statement about the half it does not hold: the note that
    sends the reader to each member's own page instead. Asserting the absence of a selector
    nothing emits proves nothing — it passes today and would go on passing over an editor that
    grew a config block under any other name."""
    lane = lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}])
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("tr[data-lane-row]")
    ui_page.locator("tr[data-lane-row] [data-lane-edit]").click()
    editor = ui_page.locator(f'[data-lane="{lane["id"]}"]')
    editor.wait_for()

    expect(editor).to_contain_text("on failure")          # the lane's own controls are here
    expect(editor).not_to_contain_text("Shared config")
    note = editor.locator("[data-lane-config-note]")
    expect(note).to_be_visible()
    expect(note).to_contain_text("each member's own settings")
    expect(note).to_contain_text("on the member's own page")


def test_expanded_lane_rows_drag_to_reorder(ui, ui_page, make_routine):
    """User order 2026-08-13: in an EXPANDED lane in the routines table, the member rows are
    the fire order — dragging one onto a sibling reorders the lane (drop below the target's
    midline lands after it). The store must carry the new order."""
    import time

    make_routine(slug="gm1")
    make_routine(slug="gm2")
    lane = lanes.create(ui.routines, name="Ordered",
                        members=[{"slug": "gm1"},
                                 {"slug": "gm2"}])
    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'tr[data-lane-row="{lane["id"]}"]')
    row.wait_for()
    row.get_by_text("⛓ Ordered").click()                     # expand → rows in fire order
    src = ui_page.locator('tr[data-drag-member="gm1"]')
    tgt = ui_page.locator('tr[data-drag-member="gm2"]')
    expect(src).to_be_visible()
    # Drive the HTML5 drag handlers with dispatched DragEvents + a real DataTransfer (the
    # documented Playwright pattern) — its mouse-gesture drag does not start Chromium's
    # native HTML5 drag reliably in headless, which is why weekgrid went pointer-based.
    box = tgt.bounding_box()
    y = box["y"] + box["height"] * 0.8                       # below the midline = "after"
    dt = ui_page.evaluate_handle("() => new DataTransfer()")
    src.dispatch_event("dragstart", {"dataTransfer": dt})
    tgt.dispatch_event("dragover", {"dataTransfer": dt, "clientY": y})
    tgt.dispatch_event("drop", {"dataTransfer": dt, "clientY": y})

    def members():
        rec = lanes.get(ui.routines, lane["id"])
        return [m["slug"] for m in (rec["members"] if rec else [])]

    deadline = time.time() + 8
    while time.time() < deadline and members() != ["gm2", "gm1"]:
        time.sleep(0.15)
    assert members() == ["gm2", "gm1"], \
        f"drag did not reorder the lane: {members()}"


def _open_editor(ui, ui_page, lane_id):
    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'tr[data-lane-row="{lane_id}"]')
    row.wait_for()
    row.locator("[data-lane-edit]").click()
    editor = ui_page.locator(f'[data-lane="{lane_id}"]')
    editor.wait_for()
    return editor


def _shown_members(editor):
    return editor.locator("[data-member]").evaluate_all(
        "rows => rows.map((r) => r.dataset.member)")


def _members(ui, lane_id):
    rec = lanes.get(ui.routines, lane_id)
    return [m["slug"] for m in (rec["members"] if rec else [])]


def test_a_refused_member_edit_leaves_the_editor_on_what_the_store_holds(ui, ui_page,
                                                                          make_routine):
    """Every editor control PATCHes and re-renders from the reply. The member buttons edited the
    lane record IN PLACE before sending it, so when the server refused the edit, the re-render
    drew the refused list as if it had been saved — and the record was the dashboard's own copy
    of the lane, edited behind its back."""
    make_routine(slug="gm1")
    lane = lanes.create(ui.routines, name="Ordered", members=[{"slug": "uir"}, {"slug": "gm1"}])
    editor = _open_editor(ui, ui_page, lane["id"])
    ui_page.route(f"**/api/lanes/{lane['id']}", lambda route: (
        route.fulfill(status=400, json={"detail": "refused by the store"})
        if route.request.method == "PATCH" else route.continue_()))

    editor.locator('[data-member="uir"]').get_by_role("button", name="remove").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("refused by the store")
    expect(editor.locator("[data-member]")).to_have_count(2)
    editor.locator('[data-member="gm1"]').get_by_role("button", name="↑").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("refused by the store")
    assert _shown_members(editor) == ["uir", "gm1"]


def test_a_double_click_on_a_member_button_saves_one_move(ui, ui_page, make_routine):
    """A second click before the first PATCH's reply acted on rows drawn from the state before
    it: two clicks on ↓ swapped the pair and then swapped it straight back, so the store kept
    the original order while the reader had asked to move a member. One save at a time."""
    make_routine(slug="gm1")
    make_routine(slug="gm2")
    lane = lanes.create(ui.routines, name="Ordered",
                        members=[{"slug": "uir"}, {"slug": "gm1"}, {"slug": "gm2"}])
    editor = _open_editor(ui, ui_page, lane["id"])

    editor.locator('[data-member="uir"]').get_by_role("button", name="↓").dblclick()
    until(lambda: _members(ui, lane["id"]) != ["uir", "gm1", "gm2"], page=ui_page,
          what="the member move")
    ui_page.wait_for_timeout(500)                       # any second save has landed by now
    assert _members(ui, lane["id"]) == ["gm1", "uir", "gm2"]
    expect(editor.locator("[data-member]").first).to_have_attribute("data-member", "gm1")


def test_escape_closes_the_lane_editor(ui, ui_page):
    """The overlay closes on Escape — which needs focus INSIDE it. It opened with focus left on
    the ✎ button in the table behind it, so the key went nowhere until the reader clicked in."""
    lane = lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}])
    editor = _open_editor(ui, ui_page, lane["id"])
    ui_page.keyboard.press("Escape")
    expect(editor).to_have_count(0)


def test_routines_page_lane_editor_catchup_policy(ui, ui_page):
    """The lane editor carries the boot catch-up policy: born run_once (a missed fire is made
    up once at the next boot), switchable to skip; the choice persists to the store."""
    rec = lanes.create(ui.routines, name="Sched", members=[{"slug": "uir"}],
                       cron="0 7 * * *", tz="UTC")
    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'tr[data-lane-row="{rec["id"]}"]')
    row.wait_for()
    row.locator("[data-lane-edit]").click()
    ui_page.locator(f'[data-lane="{rec["id"]}"]').wait_for()
    sel = ui_page.locator("[data-lane-catchup]")
    expect(sel).to_have_value("run_once")
    sel.select_option("skip")
    until(lambda: lanes.get(ui.routines, rec["id"])["catchup"] == "skip", what="the catchup save")
    expect(ui_page.locator("[data-lane-catchup]")).to_have_value("skip")   # re-rendered
