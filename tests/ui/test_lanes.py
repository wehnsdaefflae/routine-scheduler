"""Lane and domain management on the Routines page — neither has a subpage of its own (D80).

When routines fire and what config they share are two objects (docs/lanes-domains.md); this
page manages both:

- a LANE is the temporal axis. The toolbar creates one, the lane row runs/pauses it, the overlay
  editor edits members, order, schedule and on-failure; the lot persists to
  `.control/lanes.json`. A lane carries no config and no store at all — which is what
  `test_moving_a_routine_between_lanes_leaves_its_config_alone` pins, because one record holding
  both axes makes a timing decision silently a permissions decision.
- a DOMAIN is the shared config block every member inherits, edited in the DOMAINS section.
  MEMBERSHIP is deliberately not edited there: a routine names its domain in its own
  routine.yaml, so joining one is the routine page's ordinary config save and the section only
  reads the membership back. The chip on a routine's row is the way into that section.

The ROUTINE page's own two halves are here as well: the hero tile that READS this routine's
lane without offering to change it, plus the domain picker that joins one through the ordinary
config save.

Driven against the REAL console JS — the ui_page fixture also asserts the page threw no JS
error."""

import json

import yaml
from playwright.sync_api import expect

from rsched import domains, lane_runs, lanes
from rsched.config import MachineConfig

from .conftest import TOKEN

# One block per control in the domain editor — TEN of them for the ELEVEN keys a domain may
# share (`domains.CONFIG_KEYS`), because permissions and capabilities are one two-layer panel
# and the fs roots take a block each.
#: Every key a domain may share → the editor block that writes it. Keyed on the KEY, not on the
#: block, so the assertion below binds the panel to `domains.CONFIG_KEYS` itself: a twelfth
#: shareable key fails here, in Python, the moment it is declared — no browser, no waiting for
#: someone to notice. That binding is the point. The panel shipped a whole release rendering
#: seven of eleven keys with nothing red, because the only thing that knew the full set was
#: a tuple in another module. Two keys share one block (the two permission layers are one
#: control, so the map is many-to-one).
DOMAIN_BLOCK_FOR = {
    "permissions": "Permissions & capabilities",
    "capabilities": "Permissions & capabilities",
    "rules": "General rules",
    "grants": "Secrets",
    "connections": "Connections",
    "machines": "Machines",
    "fs_read_roots": "Filesystem — readable",
    "fs_write_roots": "Filesystem — writable",
    "models": "Models",
    "budgets": "Budgets",
    "tags": "Tags",
}
DOMAIN_BLOCKS = tuple(dict.fromkeys(DOMAIN_BLOCK_FOR.values()))


def test_every_shareable_key_has_an_editor_block():
    """The binding, without a browser: a key a domain may share that no block writes is a key
    an operator can neither see nor change; the only way to find out was to look.
    """
    from rsched.domains import CONFIG_KEYS

    assert set(DOMAIN_BLOCK_FOR) == set(CONFIG_KEYS)


def _join_domain(ui, slug: str, domain_id: str) -> None:
    """Put a routine in a domain the way the routine page's picker does — by writing `domain:`
    into the routine's OWN routine.yaml, which is the only place membership lives.
    """
    path = ui.routines / slug / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["domain"] = domain_id
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _open_domain_editor(ui, ui_page, domain_id: str):
    """The shared-config editor for one domain, reached the way an operator reaches it: from
    that domain's row in the Routines page's domains section, which is the only surface either
    object has (D80)."""
    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'[data-domain-row="{domain_id}"]')
    row.wait_for(timeout=10_000)
    row.locator("[data-domain-edit]").click()
    panel = ui_page.locator(f'[data-domain-config="{domain_id}"]')
    expect(panel).to_be_visible(timeout=10_000)
    return panel


def _shared(ui, ui_page, domain_id: str, key: str):
    """One key of the domain's STORED config, polled until the save lands. Every control in the
    panel writes through the API, so the store is where a save is confirmed — a toast reports
    only what the page believes."""
    for _ in range(50):
        value = (domains.get(ui.routines, domain_id) or {}).get("config", {}).get(key)
        if value:
            return value
        ui_page.wait_for_timeout(100)
    return None


def _detail(ui, ui_page, slug: str) -> dict:
    """The routine's EFFECTIVE config as the console reads it — its own routine.yaml with its
    domain's shared block merged in.
    """
    r = ui_page.request.get(f"{ui.url}/api/routines/{slug}",
                            headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.ok, r.status
    return r.json()


def test_routine_page_hero_reports_the_lane_without_offering_to_change_it(ui, ui_page):
    """The hero READS this routine's lane and links to where lanes are edited. A lane orders
    several routines and belongs to no single one of them, so it is instance state the Routines
    page owns; a picker here would sit among controls that are otherwise all this routine's own
    config, which is how a timing decision turns into a permissions change by side effect. What
    the routine page DOES own is the domain picker, further down."""
    lanes.create(ui.routines, name="Nightly", members=[])
    ui_page.goto(f"{ui.url}/#/routine/uir")
    tile = ui_page.locator("[data-hero-lane]")
    expect(tile).to_be_visible(timeout=10_000)
    expect(tile.locator(".hero-strong")).to_have_text("none", timeout=10_000)
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
    expect(tile.locator(".hero-strong")).to_have_text("Unscheduled", timeout=10_000)
    expect(tile.locator(".hero-sub")).to_contain_text("its own cron applies")
    expect(tile.locator("a.hero-link")).to_have_attribute("href", "#/routines")


def test_hero_lane_tile_says_a_scheduled_lane_drives_the_fires(ui, ui_page):
    """The same tile for a SCHEDULED lane (D71): the chain fires the members in order, so this
    routine's own cron is suppressed and the sub-line says which of the two is in charge."""
    lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}], cron="0 3 * * *")
    ui_page.goto(f"{ui.url}/#/routine/uir")
    tile = ui_page.locator("[data-hero-lane]")
    expect(tile.locator(".hero-strong")).to_have_text("Nightly", timeout=10_000)
    expect(tile.locator(".hero-sub")).to_contain_text("fires via the lane's chain")


def test_routines_page_lane_crud(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("[data-lane-new]", timeout=10_000)

    # create: the toolbar's "+ new lane" opens the overlay form
    ui_page.locator("[data-lane-new]").click()
    ui_page.locator("[data-lane-new-name]").fill("Morning")
    picker = ui_page.locator("[data-lane-members]")
    expect(picker.locator("option")).to_have_count(1)
    expect(picker.locator("option")).to_have_text("Test uir")
    picker.select_option("uir")
    ui_page.get_by_role("button", name="add lane").click()

    row = ui_page.locator("tr[data-lane-row]")
    row.wait_for(timeout=10_000)
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
    editor.wait_for(timeout=10_000)
    expect(editor.locator('[data-member="uir"]')).to_contain_text("uir")
    ui_page.locator("[data-lane-editor-close]").click()

    # Run now → arms a sequential fire; the row shows the chain progress and the
    # in-flight chain snapshots the member records
    ui_page.locator("tr[data-lane-row]", has_text="Morning").get_by_text("⛓ Morning").click()
    ui_page.locator("[data-lane-run]").click()
    expect(ui_page.locator("[data-lane-progress]")).to_contain_text(
        "1/1", timeout=10_000)
    flight = lane_runs.read(ui.routines, lane_id)
    assert flight is not None and flight["cursor"] == 0
    assert flight["members"] == [{"slug": "uir"}]
    # clear the armed chain so the delete-and-empty-store assertions below stay clean
    lane_runs.remove(ui.routines, lane_id)

    # change the instance default → persists
    ui_page.locator("[data-lanes-default]").select_option("continue")
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("continue")
    ui_page.wait_for_timeout(300)
    assert stored()["default_on_failure"] == "continue"

    # delete (from the editor; confirm dialog → confirm)
    ui_page.locator("tr[data-lane-row] [data-lane-edit]").click()
    ui_page.locator(f'[data-lane="{lane_id}"]').wait_for(timeout=10_000)
    ui_page.get_by_role("button", name="delete lane").click()
    ui_page.get_by_role("button", name="delete", exact=True).last.click()
    expect(ui_page.locator("tr[data-lane-row]")).to_have_count(0, timeout=10_000)
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
    sched_row.wait_for(timeout=10_000)

    # only the scheduled row offers the toggle
    expect(sched_row.locator("[data-lane-pause-toggle]")).to_have_text("⏸ pause")
    expect(ui_page.locator(
        f'tr[data-lane-row="{plain["id"]}"] [data-lane-pause-toggle]')).to_have_count(0)

    # pause → badge appears, store carries paused=true
    sched_row.locator("[data-lane-pause-toggle]").click()
    expect(ui_page.locator(
        f'tr[data-lane-row="{rec["id"]}"] [data-lane-paused]')).to_contain_text(
        "paused", timeout=10_000)
    assert lanes.get(ui.routines, rec["id"])["paused"] is True

    # resume → badge gone, store cleared (the row re-renders, so re-locate)
    ui_page.locator(f'tr[data-lane-row="{rec["id"]}"] [data-lane-pause-toggle]').click()
    expect(ui_page.locator(
        f'tr[data-lane-row="{rec["id"]}"] [data-lane-paused]')).to_have_count(
        0, timeout=10_000)
    ui_page.wait_for_timeout(300)   # give the PATCH a beat, as the CRUD test does
    assert lanes.get(ui.routines, rec["id"])["paused"] is False


def test_no_lane_or_group_subpage_exists(ui, ui_page):
    """Lanes and domains are managed on the Routines page, so neither has a subpage: #/lanes
    hits the router's fallback (the Conversations landing) rather than a broken view. `groups`
    is named here ON PURPOSE: it is a DEAD route operators still hold bookmarks to (D80), so it
    has to land somewhere real rather than on a view that throws."""
    # a route that never existed, then the dead one a bookmark can still ask for
    for route in ("lanes", "groups"):
        ui_page.goto(f"{ui.url}/#/{route}")
        ui_page.wait_for_url(f"{ui.url}/#/", timeout=10_000)


def test_domains_section_edits_the_shared_config(ui, ui_page):
    """D82 on the shared-surface axis: the DOMAINS section is where the block every member
    inherits is edited. Exercises the real panel end to end — it mounts the ROUTINE page's own
    permissions control; a save lands in .control/domains.json as the domain's config.

    Membership rides along read-only: it is read from the routines that NAME this domain, so
    the section can show who is in it without owning the list."""
    from rsched import secrets

    secrets.set_secret("FAU_TOKEN", "s3cret")
    dom = domains.create(ui.routines, name="FAU")
    _join_domain(ui, "uir", dom["id"])

    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'[data-domain-row="{dom["id"]}"]')
    row.wait_for(timeout=10_000)
    expect(row).to_contain_text("FAU")
    expect(row).to_contain_text("uir")          # membership, read back from the routines

    row.locator("[data-domain-edit]").click()
    panel = ui_page.locator(f'[data-domain-config="{dom["id"]}"]')
    expect(panel).to_be_visible(timeout=10_000)
    expect(panel).to_contain_text("inherits")

    # a save writes the domain's config (a secret grant is the simplest control to drive
    # headlessly AND the one whose result is visible in the store)
    panel.locator('[data-domain-secret="FAU_TOKEN"]').check()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text(
        "FAU_TOKEN", timeout=10_000)
    ui_page.wait_for_timeout(300)
    assert domains.get(ui.routines, dom["id"])["config"]["grants"] == {
        "secret:FAU_TOKEN": True}


def test_domain_editor_covers_every_shareable_key(ui, ui_page):
    """A domain may share ELEVEN routine.yaml keys (`domains.CONFIG_KEYS`) and each one is
    editable here. A key with no control is a key a migrated domain carries invisibly: the save
    path spreads the whole block, so nothing is destroyed — nothing can be changed either, which
    leaves the config a member inherits answering to a surface that does not exist.

    `deliberation` is the one control the routine page's neighbouring sections would bring
    along that must NOT be here. It is a tuning.yaml handle rather than routine.yaml config, so
    it is not among the shareable keys — a slider here would look exactly like the ones beside
    it and write nothing at all."""
    dom = domains.create(ui.routines, name="FAU")
    panel = _open_domain_editor(ui, ui_page, dom["id"])
    for title in DOMAIN_BLOCKS:
        expect(panel.locator(f'[data-dcfg-block="{title}"]')).to_be_visible()
    # exactly these: a count pins the absent control too, whatever a later copy-paste names it
    expect(panel.locator("[data-dcfg-block]")).to_have_count(len(DOMAIN_BLOCKS))
    expect(panel.locator(".delib")).to_have_count(0)
    expect(panel).not_to_contain_text("deliberation")


def test_domain_shares_machines_models_budgets_and_tags(ui, ui_page):
    """The four controls round-trip to the store. What the member reads back then shows the two
    merge halves at once: machines and tags are LIST keys that union onto the member's own,
    while models and budgets are MAPPINGS merged per key with the member's value winning — the
    shared `main` model reaches a routine that binds no model, the shared ceiling loses to the
    `max_turns` that routine sets itself.

    Each ceiling is shared on its OWN: one filled budget row saves while the rest stay blank,
    because a layer that had to fill all eight would impose seven values nobody chose."""
    mac = MachineConfig(host="10.0.0.9", user="rsched", description="RTX 4090", tags=["gpu"])
    mac.name = "gpu-box"
    ui.server_cfg.machines = {"gpu-box": mac}   # the live catalog the API reads
    dom = domains.create(ui.routines, name="FAU")
    _join_domain(ui, "uir", dom["id"])
    panel = _open_domain_editor(ui, ui_page, dom["id"])

    # Each save is awaited to its acknowledgement before the next one starts: every control
    # PATCHes the WHOLE block built from the record the last answer returned, so a click that
    # overtakes the answer before it would write a block missing the key just stored.
    toast = ui_page.locator("#toast:not([hidden])")

    machines = panel.locator('[data-dcfg-block="Machines"]')
    machines.locator("label", has_text="gpu-box").locator("input[type=checkbox]").check()
    machines.get_by_role("button", name="save machines").click()
    expect(toast).to_contain_text("machines saved", timeout=10_000)
    assert _shared(ui, ui_page, dom["id"], "machines") == ["gpu-box"]

    models = panel.locator('[data-dcfg-block="Models"]')
    models.locator('[data-domain-model="main"]').select_option("m")
    models.locator("[data-domain-models-save]").click()
    expect(toast).to_contain_text("domain models saved", timeout=10_000)
    assert _shared(ui, ui_page, dom["id"], "models") == {"main": "m"}

    budgets = panel.locator('[data-dcfg-block="Budgets"]')
    budgets.locator('[data-domain-budget="max_turns"]').fill("42")
    budgets.locator("[data-domain-budgets-save]").click()
    expect(toast).to_contain_text("domain budgets saved", timeout=10_000)
    assert _shared(ui, ui_page, dom["id"], "budgets") == {"max_turns": 42}

    # the tag editor has no button: every change saves, the chip appears once it landed
    tags = panel.locator('[data-dcfg-block="Tags"]')
    tags.locator(".tags input").fill("fau")
    tags.locator(".tags input").press("Enter")
    expect(tags.locator(".tag", has_text="fau")).to_be_visible(timeout=10_000)
    assert _shared(ui, ui_page, dom["id"], "tags") == ["fau"]

    detail = _detail(ui, ui_page, "uir")
    assert detail["machines"] == ["gpu-box"]
    assert detail["models"]["main"] == "m"
    assert detail["tags"] == ["fau"]
    assert detail["budgets"]["max_turns"] == 10        # the member's own ceiling stands
    assert set(detail["inherited"]) >= {"machines", "models", "tags"}
    # budgets is absent from the provenance: the one ceiling the domain sets is one the member
    # sets too, so the domain contributed nothing to report
    assert "budgets" not in detail["inherited"]


def test_routine_page_domain_picker_joins_a_domain(ui, ui_page):
    """The other axis, from the routine's own page: the domain picker saves through the ORDINARY
    routine config PATCH (`domain`), so at-most-one is a fact of the file rather than a rule
    someone has to enforce across a list — and the domain's membership is that file, read
    back."""
    dom = domains.create(ui.routines, name="FAU", config={"permissions": ["memory"]})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    sel = ui_page.locator("[data-domain-sel]")
    expect(sel).to_be_visible(timeout=10_000)
    expect(sel.locator("option")).to_have_count(2)          # none + FAU
    sel.select_option(dom["id"])
    expect(ui_page.locator("#toast:not([hidden])")).to_be_visible(timeout=10_000)

    cfg_path = ui.routines / "uir" / "routine.yaml"
    for _ in range(50):
        if yaml.safe_load(cfg_path.read_text(encoding="utf-8")).get("domain") == dom["id"]:
            break
        ui_page.wait_for_timeout(100)
    assert yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["domain"] == dom["id"]
    assert domains.members(ui.routines, dom["id"]) == ["uir"]
    # and the shared block reaches the member from there
    assert _detail(ui, ui_page, "uir")["inherited_from"] == "FAU"


def test_moving_a_routine_between_lanes_leaves_its_config_alone(ui, ui_page):
    """The clearest behavioural consequence of the two axes being two objects
    (docs/lanes-domains.md). A lane owns no config and no store: the shared block belongs to the
    DOMAIN the routine names in its own routine.yaml, so no lane edit can reach it. With one
    record holding both axes, moving a member from one lane to another silently changes its
    effective permissions — a timing decision doing the work of a permissions decision, with
    nothing on the page to say so.
    """
    dom = domains.create(ui.routines, name="FAU",
                         config={"permissions": ["memory"], "fs_read_roots": ["/srv/fau"]})
    _join_domain(ui, "uir", dom["id"])
    nightly = lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}])
    weekly = lanes.create(ui.routines, name="Weekly", members=[])

    before = _detail(ui, ui_page, "uir")
    assert before["inherited_from"] == "FAU"
    assert "permissions" in before["inherited"] and "fs_read_roots" in before["inherited"]

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
    for key in ("inherited", "inherited_from", "permissions", "capabilities",
                "fs_read_roots", "fs_write_roots", "rules", "grants"):
        assert after[key] == before[key], f"the lane move changed {key}"


def test_lane_editor_offers_no_shared_config(ui, ui_page):
    """The same invariant from the other side: there is nothing IN a lane editor that could
    change a member's config. Shared config belongs to the DOMAIN, so the editor offers members,
    order, schedule and on-failure and nothing else.

    The pin is the editor's POSITIVE statement about the half it does not hold: the note that
    sends the reader to the domain surface instead. Asserting the absence of a selector nothing
    emits proves nothing — it passes today and would go on passing over an editor that grew a
    shared-config block under any other name."""
    lane = lanes.create(ui.routines, name="Nightly", members=[{"slug": "uir"}])
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("tr[data-lane-row]", timeout=10_000)
    ui_page.locator("tr[data-lane-row] [data-lane-edit]").click()
    editor = ui_page.locator(f'[data-lane="{lane["id"]}"]')
    editor.wait_for(timeout=10_000)

    expect(editor).to_contain_text("on failure")          # the lane's own controls are here
    expect(editor).not_to_contain_text("Shared config")
    note = editor.locator("[data-lane-domain-note]")
    expect(note).to_be_visible()
    expect(note).to_contain_text("a DOMAIN, not a lane")
    expect(note).to_contain_text("names its domain in its own config")
    expect(editor.locator("[data-domain-config]")).to_have_count(0)


def test_routine_row_domain_chip_reveals_that_domain(ui, ui_page, make_routine):
    """The domain chip on a routine's row is the ONLY path from the routine to the domain it
    shares a config block with: it opens the Domains section — collapsed here, as an operator
    who closed it last time would find it — and scrolls that domain's row into view. The lane
    chip beside it opens the lane's editor, so a domain chip that did nothing would be a dead
    control sitting next to a live one that looks identical."""
    make_routine(slug="uir2")
    make_routine(slug="uir3")
    dom = domains.create(ui.routines, name="FAU")
    _join_domain(ui, "uir", dom["id"])

    ui_page.add_init_script("localStorage.setItem('rsched_dash_domains', 'closed')")
    ui_page.goto(f"{ui.url}/#/routines")
    chip = ui_page.locator("button.chip.domain-chip")
    expect(chip).to_have_text("◈ FAU", timeout=10_000)
    # the chip is a button styled by base.css alone — an inline cursor here is how the design
    # system erodes, one control at a time
    assert chip.get_attribute("style") is None
    assert chip.evaluate("(n) => getComputedStyle(n).cursor") == "pointer"

    panel = ui_page.locator("[data-domains]")
    expect(panel).not_to_have_attribute("open", "")
    row = ui_page.locator(f'[data-domain-row="{dom["id"]}"]')
    expect(row).to_be_hidden()

    chip.click()
    expect(panel).to_have_attribute("open", "", timeout=10_000)
    expect(row).to_be_in_viewport(timeout=10_000)


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
    row.wait_for(timeout=10_000)
    row.get_by_text("⛓ Ordered").click()                     # expand → rows in fire order
    src = ui_page.locator('tr[data-drag-member="gm1"]')
    tgt = ui_page.locator('tr[data-drag-member="gm2"]')
    expect(src).to_be_visible(timeout=10_000)
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
