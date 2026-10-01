"""Routine detail page: the four MESSAGE folders (D74), the routine's OWN secrets (D103),
the sections side-TOC (like Settings), the settings groups and their one accept, and the
filesystem-root directory picker (browse the server FS, pick a real path — no more free-text
textarea)."""

import json
import re

import yaml
from playwright.sync_api import expect

from rsched import reports

from .conftest import until


def _unfold(page) -> None:
    """Open every routine-page settings group and each group's "more" menu.

    The page ships with only its two leading groups open (views/routine-config.js): seven open at
    once made it 11-12 000px tall. The rarely needed sections fold once more behind each group's
    "more". A control inside a fold is not visible, so a test that reads one unfolds first. What
    the DEFAULT is — and that the choice is remembered — is pinned in test_routine_groups.py, not
    here.
    """
    page.wait_for_selector(".rgroup-head")
    page.evaluate("() => { for (const d of document.querySelectorAll('details.rgroup, details.rmore')) d.open = true; }")

def test_messages_inbox_compose_edit_withdraw(ui, ui_page):
    """The Messages section's inbox folder is the routine-bound home for a note the next
    run reads at boot (F233/D74): queueing lands a msg-* file in the routine's inbox, an
    edit rewrites the SAME file in place, withdraw removes it."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Messages')")
    box = ui_page.locator('textarea[data-persist="nextrun-msg-uir"]')
    expect(box).to_be_visible()
    box.fill("re-check the freelance portals after the login fix")
    ui_page.get_by_role("button", name="queue for the next run").click()
    expect(_toast(ui_page)).to_contain_text("next run reads it")

    inbox = ui.routine_dir("uir") / "inbox"
    card = ui_page.locator(".msg-item.inbox", has_text="re-check the freelance portals")
    expect(card).to_be_visible()
    expect(card).to_contain_text("you")            # web-queued → labelled as the user's own
    msgs = list(inbox.glob("msg-*.json"))
    assert len(msgs) == 1
    assert "re-check the freelance portals" in json.loads(msgs[0].read_text())["text"]

    # edit in place: the SAME file is rewritten, never a second message
    card.locator("button", has_text="edit").click()
    ta = ui_page.locator(".msg-item.inbox textarea")
    expect(ta).to_have_value("re-check the freelance portals after the login fix")
    ta.fill("only the login fix, portals can wait")
    ui_page.locator(".msg-item.inbox button", has_text="save").click()
    expect(ui_page.locator(".msg-item.inbox", has_text="portals can wait")).to_be_visible()
    msgs = list(inbox.glob("msg-*.json"))
    assert len(msgs) == 1
    assert json.loads(msgs[0].read_text())["text"] == "only the login fix, portals can wait"

    ui_page.locator(".msg-item.inbox button", has_text="withdraw").click()
    expect(ui_page.locator(".msg-item.inbox")).to_have_count(0)
    expect(ui_page.locator(".msg-empty")).to_be_visible()
    assert list(inbox.glob("msg-*.json")) == []


def test_messages_folders_and_outbox_retract(ui, ui_page, make_routine):
    """The other three folders: read shows what runs consumed (read-only, linked to the
    consuming run), received shows picked-up hand-offs, and outbox carries the ONE write —
    retracting a not-yet-consumed addressed report removes the delivery from the target's
    inbox and the ledger records it (docs/messages.md)."""
    peer = make_routine(slug="peer")
    consumed = ui.routine_dir("uir") / "runs" / "20260101-000000" / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    (consumed / "msg-0.json").write_text(
        json.dumps({"text": "old note", "ts": "2025-12-31T00:00:00"}), encoding="utf-8")
    _, rid, _ = reports.file_report(ui.routines, routine="uir", run_id="uir:1",
                                 title="pending hand-off", detail="please fix it",
                                 target="peer", target_dir=peer)
    _, rid2, _ = reports.file_report(ui.routines, routine="uir", run_id="uir:1",
                                  title="landed hand-off", target="peer", target_dir=peer)
    reports.stamp_delivered(ui.routines, [{"report": rid2}], run_id="peer:20260102-000000")

    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Messages')")
    expect(ui_page.locator(".msg-tabs .tag", has_text="outbox · 1")).to_be_visible()

    ui_page.locator(".msg-tabs .tag", has_text="read · 1").click()
    read_card = ui_page.locator(".msg-item.read", has_text="old note")
    expect(read_card).to_be_visible()
    expect(read_card.locator("a", has_text="consumed by run")).to_have_attribute(
        "href", "#/run/uir:20260101-000000")
    expect(read_card.locator("button")).to_have_count(0)          # history is read-only
    # an absent optional (this row has no report id) must render NOTHING — a ternary null
    # fed to the native DOM append stringifies to a literal "null" (regression, 2026-08-12)
    assert "null" not in read_card.inner_text()

    ui_page.locator(".msg-tabs .tag", has_text="received · 1").click()
    received = ui_page.locator(".msg-item.received", has_text="landed hand-off")
    expect(received).to_contain_text("→ peer")
    expect(received.locator("a", has_text="picked up")).to_be_visible()
    assert "null" not in received.inner_text()                    # detail is empty here

    ui_page.locator(".msg-tabs .tag", has_text="outbox · 1").click()
    out = ui_page.locator(".msg-item.outbox", has_text="pending hand-off")
    expect(out).to_contain_text("please fix it")
    out.locator("button", has_text="retract").click()
    ui_page.locator(".modal-overlay button", has_text="retract").click()
    expect(_toast(ui_page)).to_contain_text("retracted")
    expect(ui_page.locator(".msg-item.outbox")).to_have_count(0)
    expect(ui_page.locator(".msg-tabs .tag", has_text="outbox · 0")).to_be_visible()
    assert not (peer / "inbox" / f"msg-rep-{rid}.json").exists()
    rows = {r["id"]: r for r in reports.read_reports(reports.reports_path(ui.routines))}
    assert rows[rid]["retracted"]["ts"]


def _toast(page):
    return page.locator("#toast:not([hidden])")


def test_own_secrets_set_shadow_and_remove(ui, ui_page):
    """D103: the routine's own secrets store, written from its own page. A name that also
    exists centrally is labelled as shadowing it — a value silently overriding a shared one
    is exactly the confusion the single flat namespace used to cause — and the API answers
    with NAMES, so the value never returns to the browser."""
    from rsched import secrets

    secrets.set_secret("SFTP_USER", "the-shared-one")
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.wait_for_selector("h2:has-text('Own secrets')")

    ui_page.locator("[data-own-secret-key]").fill("SFTP_USER")
    ui_page.locator("[data-own-secret-value]").fill("mine-only")
    ui_page.locator("[data-own-secret-set]").click()
    expect(_toast(ui_page)).to_contain_text("SFTP_USER saved")

    row = ui_page.locator('[data-own-secret="SFTP_USER"]')
    expect(row).to_be_visible()
    expect(row).to_contain_text("overrides the central store")
    assert secrets.load_routine_secrets("uir") == {"SFTP_USER": "mine-only"}
    assert secrets.load_secrets()["SFTP_USER"] == "the-shared-one"     # central untouched

    row.get_by_role("button", name="remove").click()
    expect(_toast(ui_page)).to_contain_text("SFTP_USER removed")
    expect(ui_page.locator('[data-own-secret="SFTP_USER"]')).to_have_count(0)
    assert secrets.load_routine_secrets("uir") == {}


def test_sections_side_toc(ui, ui_page):
    """The routine page grows an "On this page" index in the navigation rail listing its <h2>
    sections — the same mountToc index Settings gets (routine.js's recipe file tree is a
    within-section nav and no longer suppresses it). Asserted at a laptop width, which is where
    a 13 000px page most needs it and where the old right-margin rail had nothing."""
    ui_page.set_viewport_size({"width": 1425, "height": 950})
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.wait_for_selector("h2:has-text('Filesystem roots')")
    toc = ui_page.locator(".side-toc")
    expect(toc).to_be_visible()
    expect(toc).to_contain_text("Filesystem roots")
    expect(toc).to_contain_text("Budgets")

    # D57 phase 2: every config section is now built from the shared settingsSection primitive
    # (heading + .panel + a per-control description) — the SAME primitive the conversation
    # composer uses, so a setting reads and looks identical wherever it appears. The <h2>s the
    # TOC rides are still emitted (asserted above); confirm the primitive's presentation is in
    # use: the sections render inside panels, and the per-control copy renders as a description.
    expect(ui_page.locator(".panel").first).to_be_visible()
    budgets = ui_page.locator("h2:has-text('Budgets')").locator(
        "xpath=following-sibling::div[contains(@class,'panel')][1]")
    expect(budgets.locator(".muted.small").first).to_contain_text("per-run ceilings")


def test_description_editor_is_multiline(ui, ui_page):
    """msg-2 (2026-09-03): the editable routine-description field is a multi-line <textarea>,
    not a single-line <input> — so the operator can write more than one line of summary."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.wait_for_selector("h2:has-text('Description')")
    section = ui_page.locator("h2:has-text('Description')").locator(
        "xpath=following-sibling::div[contains(@class,'panel')][1]")
    ta = section.locator("textarea")
    expect(ta).to_be_visible()
    # the old single-line text input is gone
    assert section.locator("input[type='text']").count() == 0
    # and the field genuinely holds more than one line
    ta.fill("line one\nline two")
    assert "\n" in ta.input_value()


def test_fs_root_directory_picker(ui, ui_page):
    """The fs-roots editor is a real directory picker: the old textarea is gone, and browsing
    to a server directory and selecting it adds it as a root — to the draft, saved by the
    page's one accept. The write list is named for what it grants: read-write roots."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.wait_for_selector("h2:has-text('Filesystem roots')")
    # the free-text "one path per line" textarea is gone
    assert ui_page.locator("textarea[placeholder*='one path per line']").count() == 0
    roots = ui_page.locator("#sec-fs-roots + .panel")
    expect(roots.locator(".field > span")).to_have_text(["read-only roots", "read-write roots"])

    roots.locator("button:has-text('add directory')").first.click()
    picker = ui_page.locator(".dirpicker")
    expect(picker).to_be_visible(timeout=5_000)
    # jump to the fixture home and descend into its routines/ dir, then select it
    picker.locator("input.code").fill(str(ui.tmp))
    picker.locator("button:has-text('go')").click()
    picker.locator(".dp-row", has_text="routines").click()
    picker.get_by_text("select this folder").click()

    expect(picker).to_have_count(0)   # modal closed
    row = ui_page.locator(".root-path")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("routines")
    ui_page.locator(".accept-bar [data-accept]").click()
    path = ui.routine_dir("uir") / "routine.yaml"
    until(lambda: any(str(p).endswith("/routines")
                      for p in yaml.safe_load(path.read_text(encoding="utf-8"))
                      .get("fs_read_roots") or []), what="the accepted read root")


def test_the_folder_picker_keeps_its_height_whatever_the_folder_holds(ui, ui_page):
    """A dialog that grows and shrinks with every folder moves its own buttons out from under
    the pointer. The list scrolls instead; the dialog stays where it opened."""
    for i in range(40):
        (ui.tmp / "many" / f"folder-{i:02d}").mkdir(parents=True)
    (ui.tmp / "few").mkdir()
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.locator("#sec-fs-roots + .panel button:has-text('add directory')").first.click()
    picker = ui_page.locator(".dirpicker")
    listing = picker.locator(".dirpicker-list")
    heights = []
    for folder, rows in (("few", 1), ("many", 41)):
        picker.locator("input.code").fill(str(ui.tmp / folder))
        picker.locator("button:has-text('go')").click()
        # the daemon answers with the RESOLVED path, which may sit behind a symlinked /tmp
        expect(picker.locator("input.code")).to_have_value(re.compile(f"/{folder}$"))
        expect(listing.locator(".dp-row")).to_have_count(rows)    # ".." and its folders
        heights.append((round(listing.bounding_box()["height"]),
                        round(picker.bounding_box()["height"])))
    assert heights[0] == heights[1], f"the picker changed size with its folder: {heights}"
    picker.get_by_role("button", name="cancel").click()


def test_runs_table_caps_at_ten_with_show_all(ui, ui_page):
    """F345 (user order 2026-08-15): the Runs element grew as tall as the whole run
    history and pushed every section below the fold — it now shows the 10 newest rows
    and the full history opens only on an explicit "show all" click (reversible)."""
    for i in range(12):
        ui.seed_run("uir", f"202607{i + 1:02d}-000000", "finished", summary=f"run {i}")
    ui_page.goto(f"{ui.url}#/routine/uir")
    rows = ui_page.locator(".runs-box tbody tr")
    expect(rows).to_have_count(10)                       # capped, however many exist
    btn = ui_page.locator(".runs-box button", has_text="show all")
    expect(btn).to_be_visible()
    btn.click()
    expect(ui_page.locator(".runs-box button", has_text="show fewer")).to_be_visible()
    assert rows.count() >= 12, "expanding must render the full history"


def _recipe_with_a_stage(ui) -> None:
    stages = ui.routine_dir("uir") / "stages"
    stages.mkdir()
    (stages / "gather.md").write_text("# Gather\n\nthe stage body\n", encoding="utf-8")


def test_a_slow_recipe_file_never_lands_over_the_one_picked_after_it(ui, ui_page):
    """The recipe editor fetched a file and THEN painted its pane, with nothing asking whether
    the reader had picked another meanwhile: pick main.md, then a stage, and a main.md answer
    arriving last replaced the stage's editor — under a tree still highlighting the stage, with
    the URL rewritten to main.md. A save from that pane wrote the file nobody was looking at."""
    _recipe_with_a_stage(ui)
    held = []

    def hold_main(route):
        if "path=main.md" in route.request.url:
            held.append(route)
        else:
            route.continue_()
    ui_page.route("**/api/routines/uir/file?*", hold_main)
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    nav = ui_page.locator(".recipe-navcol")
    nav.locator(".rn-file", has_text="main").click()
    until(lambda: held, what="the held main.md read", page=ui_page)
    nav.locator(".rn-file", has_text="gather").click()
    pane = ui_page.locator(".recipe-editorcol .ref-tag")
    expect(pane).to_have_text("stages/gather.md")

    held[0].continue_()                        # main.md's answer arrives last
    ui_page.wait_for_timeout(600)
    assert pane.inner_text() == "stages/gather.md", "a stale read painted over the newer pick"
    assert "file=stages%2Fgather.md" in ui_page.url, ui_page.url
    expect(nav.locator(".rn-file.active")).to_contain_text("gather")


def test_a_recipe_save_is_one_request_however_often_it_is_pressed(ui, ui_page):
    """The recipe's save button stayed live while its PUT was in flight, so a double press
    wrote the file twice. util.act() is the console's one save-button shape: disabled until the
    request settles, re-enabled in a `finally`."""
    puts = []

    def hold_put(route):
        if route.request.method == "PUT":
            puts.append(route)
        else:
            route.continue_()
    ui_page.route("**/api/routines/uir/file", hold_put)
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.locator(".recipe-navcol .rn-file", has_text="main").click()
    ui_page.locator(".recipe-editorcol textarea").fill("# Main\n\nrevised\n")
    save = ui_page.locator(".recipe-editorcol button", has_text="save")
    save.click()
    until(lambda: puts, what="the save's PUT", page=ui_page)
    expect(save).to_be_disabled()              # a second press cannot send a second write
    puts[0].continue_()
    expect(_toast(ui_page)).to_contain_text("main.md saved")
    expect(save).to_be_enabled()
    assert len(puts) == 1


def test_weekly_schedule_day_set_roundtrips(ui, ui_page, make_routine):
    """F347 (user order 2026-08-15, GCal-style repetitions): weekly is a SET of day
    toggles — checking Mon+Wed+Fri saves a day-list cron and reads back as the same
    checked set (server round-trip, not client state)."""
    make_routine(slug="wkly")
    ui_page.goto(f"{ui.url}#/routine/wkly")
    ui_page.wait_for_selector("h2:has-text('Schedule')")
    freq = ui_page.locator("select", has=ui_page.locator('option[value="weekly"]')).first
    freq.select_option("weekly")
    chips = ui_page.locator(".day-chip input")
    expect(chips).to_have_count(7)
    expect(chips.nth(1)).to_be_checked()          # Monday is the default set
    chips.nth(3).check()                          # Wednesday
    chips.nth(5).check()                          # Friday
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(_toast(ui_page)).to_contain_text("accepted")

    ui_page.goto(f"{ui.url}#/routine/wkly")
    ui_page.wait_for_selector(".day-chip input")
    chips = ui_page.locator(".day-chip input")
    for i in range(7):
        if i in (1, 3, 5):
            expect(chips.nth(i)).to_be_checked()
        else:
            expect(chips.nth(i)).not_to_be_checked()


def test_the_hub_tab_saves_as_identity(ui, ui_page):
    """The Steward-hub heading lives in the identity group beside the name: the accept writes
    it through the ordinary PATCH, trimmed. An empty field removes the key again."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    group = ui_page.locator(".rgroup", has=ui_page.locator(
        ".rgroup-title", has_text="Identity & recipe"))
    expect(group.locator("h2", has_text="Hub tab")).to_have_count(1)

    field = ui_page.locator("[data-hub-tab]")
    field.fill("  FAU ")
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(_toast(ui_page)).to_contain_text("accepted")
    path = ui.routine_dir("uir") / "routine.yaml"
    until(lambda: yaml.safe_load(path.read_text(encoding="utf-8")).get("hub_tab") == "FAU",
          what="the hub tab save")

    ui_page.locator("[data-hub-tab]").fill("")
    ui_page.locator(".accept-bar [data-accept]").click()
    until(lambda: "hub_tab" not in yaml.safe_load(path.read_text(encoding="utf-8")),
          what="the hub tab removal")


def test_every_settings_section_sits_in_a_named_group(ui, ui_page):
    """Every section the settings emit belongs to one of the seven groups — a heading outside
    them is a control nobody looks for. The rarely needed ones sit in a group's "more" menu,
    which is still inside the group."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector(".rgroup")
    titles = ui_page.locator(".rgroup-title").all_inner_texts()
    assert [t.strip().lower() for t in titles] == [
        "schedule & gate", "goal", "abilities", "secrets & access", "limits & reach", "models",
        "identity & recipe"]
    stray = ui_page.evaluate("""() => [...document.querySelectorAll('h2[id^="sec-"]')]
      .filter((h) => h.id !== 'sec-settings' && !h.closest('.rgroup')).map((h) => h.id)""")
    assert stray == [], f"sections outside every group: {stray}"
    for heading in ("Run gate", "Permissions & capabilities", "General rules",
                    "Shared reminders", "Effective surface", "Finish line",
                    "What a finished run delivers", "Budgets", "Own secrets", "Models",
                    "Machines", "Recipe", "State & memory", "Hub tab", "Origin"):
        expect(ui_page.locator(".rgroup-body h2", has_text=heading).first).to_have_count(1)


def test_output_compression_is_not_a_setting(ui, ui_page):
    """Lossless output compression is engine behaviour, not a per-routine decision — the page
    offers no control for it."""
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.wait_for_selector("#sec-models")
    expect(ui_page.get_by_label("Output compression", exact=True)).to_have_count(0)


def test_compression_measurement_in_transcript(ui, ui_page):
    """The engine's lossless output compression reports on the observation it shaped — what it
    did, with which engine, and the estimated saving — for the operator, never the model."""
    run = ui.seed_run("uir", "20260910-120000", "finished")
    events = [
        {"type": "assistant_action", "turn": 1,
         "payload": {"kind": "script", "name": "sample", "say": "Read the logs"}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "script", "name": "sample", "exit": 0, "stdout": "sample output",
                     "compression": {"status": "applied", "kind": "json",
                                     "baseline_chars": 8000, "candidate_chars": 2000,
                                     "estimated_tokens_saved": 1500, "elapsed_ms": 1.5}}},
    ]
    with (run / "transcript.jsonl").open("a") as f:
        f.writelines(json.dumps(e) + "\n" for e in events)
    ui_page.goto(f"{ui.url}#/run/uir:20260910-120000")
    expect(ui_page.locator(".transcript")).to_contain_text("compression: applied json")
    expect(ui_page.locator(".transcript")).to_contain_text("1500 tokens potentially saved (estimate)")


def test_archiving_a_publisher_names_what_it_leaves_behind(ui, ui_page, make_routine):
    """Archiving tidies the routine dir and its secrets and CANNOT touch what the routine
    published on another host. That was silent, and a steward card outlived its routine
    three times in two weeks — each one found by the operator's own eyes (R1658, bina).

    The console now says what is still out there and who can retire it, at the one moment
    anything knows the routine is gone.
    """
    pub = make_routine(slug="hubpub")
    cfg = yaml.safe_load((pub / "routine.yaml").read_text(encoding="utf-8"))
    cfg["fs_read_roots"] = ["/home/mark/.local/share/routine-scheduler-libraries/web/steward"]
    (pub / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ui_page.goto(f"{ui.url}#/routine/hubpub")
    ui_page.wait_for_selector("h1:has-text('hubpub')")
    ui_page.get_by_role("button", name="archive").click()
    ui_page.locator(".modal-overlay").get_by_role(
        "button", name="archive", exact=True).click()

    toast = _toast(ui_page)
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("steward hub")
    # WHERE it is, not merely that something is left: a residue nobody can find is a residue
    # nobody removes
    expect(toast).to_contain_text("_store/hubpub/")
    # and WHO can actually remove it. The kit's api.php has no delete-project op and no routine
    # has a way onto that host, so the line names the OPERATOR: a routine slug here reads as
    # "someone else will handle it", and nothing ever does.
    expect(toast).to_contain_text("ask the operator to retire it")


def test_archiving_names_every_surface_it_leaves_behind(ui, ui_page):
    """`external_residue` is a LIST — one row per outside surface the routine published to, and
    the server's surface table is built to grow. The page toasted each row in turn into the one
    #toast element, so every row but the last was overwritten before anybody could read it."""
    residue = [{"surface": "steward hub", "locator": "_store/uir/ on the steward host",
                "owner": "the operator", "note": ""},
               {"surface": "status page", "locator": "pages/uir/", "owner": "the operator",
                "note": ""}]
    ui_page.route("**/api/routines/uir/archive", lambda route: route.fulfill(
        json={"ok": True, "external_residue": residue, "lanes_left": []}))
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.get_by_role("button", name="archive").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="archive", exact=True).click()
    toast = _toast(ui_page)
    expect(toast).to_contain_text("status page")
    expect(toast).to_contain_text("steward hub")


def test_a_run_summary_is_rendered_prose_not_raw_markdown(ui, ui_page):
    """The summary column is the one thing a reader scans to decide which run to open, and it
    is MODEL PROSE. Rendered raw it was prefixed with punctuation noise on most rows, and a
    summary whose first line is a heading ran into the line under it."""
    ui.seed_run("uir", "20260715-090000", "finished",
                summary="## **Two applications** went out\nand one came back.")
    ui_page.goto(f"{ui.url}#/routine/uir")
    cell = ui_page.locator(".runs-box tbody tr td.prose").first
    expect(cell).to_be_visible()
    # the heading marker is gone, the bold is BOLD, and the second line stays out of a
    # one-line cell instead of running into the first
    expect(cell).to_have_text("Two applications went out")
    expect(cell.locator("strong")).to_have_text("Two applications")


def test_every_explanation_on_the_page_shares_one_measure(ui, ui_page):
    """A section's description and a panel's intro are ONE voice. They were two: a 68ch-capped
    `p.set-desc` in header mode and an uncapped div in panel mode, so the same copy ran to
    1 350px in one place and 450px in the other on one screen."""
    ui_page.set_viewport_size({"width": 1425, "height": 950})
    ui_page.goto(f"{ui.url}#/routine/uir")
    _unfold(ui_page)
    ui_page.wait_for_selector("h2:has-text('Budgets')")
    widths = ui_page.locator(".set-desc").evaluate_all(
        "els => els.map(e => e.getBoundingClientRect().width)")
    assert widths, "the page renders no section descriptions at all"
    assert max(widths) < 640, f"an explanation runs to {max(widths):.0f}px"
