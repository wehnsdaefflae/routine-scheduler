"""D200's card on the REAL Decisions page: a run's proposed revision to its OWN recipe.

Its own band, because the two buttons are not the queued-creation pair: nothing is CREATED here.
The proposing run already reasoned the change and could not write it (a scheduled run holds no
`write_recipe`), so "apply it" hands that run the capability back — the leg is resumed with its
recipe writable and makes the edit itself. A kind with no band falls through `pending.js
summarize()` to the lane label carrying a "create it" button that can only 400, which is the
failure that module's own comment records, so the band is what these tests are for.
"""

from __future__ import annotations

from playwright.sync_api import expect

from rsched.paths import atomic_write_json, read_json

from .conftest import until

LINE = "stages/scan.md — read state/cursor.json before the feed; two turns went on re-deriving it"


def _queue(ui, *, pid="pc-20261025-140000-bbbbbb", routine="uir", ts="20261025-140000",
           lines=(LINE,)):
    d = ui.routines / ".control" / "pending-creations"
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / f"{pid}.json", {
        "id": pid, "kind": "recipe-revision", "routine": routine,
        "run_id": f"{routine}:{ts}", "created_at": "2026-10-25T14:00:00+02:00",
        "summary": f"{routine} proposes a revision to its own recipe — {lines[0]}",
        "fields": {"lines": list(lines), "run_id": f"{routine}:{ts}"}})
    return pid


def test_the_band_shows_the_revisions_in_the_runs_own_words(ui, ui_page):
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".q-group-head", has_text="recipe revisions")).to_be_visible()
    card = ui_page.locator("[data-recipe]")
    expect(card).to_contain_text("proposes 1 revision to its own recipe")
    # the lines ARE what is being approved, so they are on the card, not folded away
    expect(card.locator("[data-recipe-line]")).to_contain_text("read state/cursor.json")
    expect(card).to_contain_text("resumes that run with its recipe writable")


def test_apply_it_resumes_the_proposing_run_and_clears_the_row(ui, ui_page):
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.get_by_role("button", name="apply it").click()
    expect(ui_page.locator("#toast")).to_contain_text("recipe writable")
    expect(ui_page.locator(".q-group-head", has_text="recipe revisions")).to_be_hidden()
    # the unlock the resumed leg reads at init (engine/revise.py), carrying the run's own words
    from rsched.paths import read_json
    marker = read_json(ui.routines / "uir" / "runs" / "20261025-140000" / "revise.json")
    assert "read state/cursor.json" in marker["instruction"]


def test_it_never_offers_the_generic_create_it_button(ui, ui_page):
    """The failure this band exists to prevent: an unmatched kind reads as "lane: ?" and gets a
    materialize button that can only 400."""
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator("[data-recipe]")).to_be_visible()
    expect(ui_page.get_by_role("button", name="create it")).to_have_count(0)
    expect(ui_page.locator(".q-group-head", has_text="queued creations")).to_have_count(0)


def test_discard_confirms_then_clears_the_row(ui, ui_page):
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.get_by_role("button", name="discard").click()
    gate = ui_page.locator(".modal-overlay")
    confirm = gate.get_by_role("button", name="discard")
    expect(confirm).to_be_disabled()                    # nothing said yet about WHY
    gate.locator("select").select_option("not wanted")      # the dialog's only select
    confirm.click()
    expect(ui_page.locator("#toast")).to_contain_text("the proposing routine was told")
    expect(ui_page.locator("[data-recipe]")).to_have_count(0)


def test_edit_and_apply_hands_the_leg_the_operators_own_wording(ui, ui_page):
    """F790, the operator's request: an EDIT option for the proposed revision. The proposal is
    prose, so editing it is rewording what the resumed leg is told to do — his version has to
    reach the unlock marker, and reach it marked as HIS.
    """
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-recipe]")
    card.get_by_role("button", name="edit", exact=True).click()
    box = card.locator("textarea.code")
    expect(box).to_have_value(LINE)                     # prefilled with what the run proposed
    box.fill("stages/scan.md — read state/cursor.json FIRST, before the feed")
    card.get_by_role("button", name="apply edited", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("your wording")
    instruction = read_json(
        ui.routines / "uir" / "runs" / "20261025-140000" / "revise.json")["instruction"]
    assert "read state/cursor.json FIRST, before the feed" in instruction
    assert "THE LINES BELOW ARE HIS" in instruction     # not passed off as the run's own
    assert "two turns went on re-deriving it" not in instruction
    expect(ui_page.locator("[data-recipe]")).to_have_count(0)


def test_cancelling_an_edit_restores_the_lines_and_applies_nothing(ui, ui_page):
    """The editor is a draft until he applies it: cancelling must leave the proposal exactly as
    the run filed it, with the card still his to decide.
    """
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-recipe]")
    card.get_by_role("button", name="edit", exact=True).click()
    card.locator("textarea.code").fill("main.md — something else entirely")
    # `apply it` would apply the RUN's lines — with his edit on screen, that click is a trap:
    # the one thing he would read it as doing is the one thing it does not do
    expect(card.get_by_role("button", name="apply it")).to_be_disabled()
    card.get_by_role("button", name="cancel", exact=True).click()
    expect(card.get_by_role("button", name="apply it")).to_be_enabled()
    expect(card.locator("textarea.code")).to_have_count(0)
    expect(card.locator("[data-recipe-line]")).to_contain_text("read state/cursor.json")
    assert not (ui.routines / "uir" / "runs" / "20261025-140000" / "revise.json").exists()


def test_discard_can_tell_the_routine_the_change_was_already_made_by_hand(ui, ui_page):
    """The card used to tell the proposing routine one thing only — that its revision "was not
    wanted" — which is false when the operator implemented it himself. Its next run then
    reasons from a lie about its own recipe, so the disposal he picks is what it is told.
    """
    _queue(ui)
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.get_by_role("button", name="discard").click()
    gate = ui_page.locator(".modal-overlay")
    gate.locator("select").select_option("already done")
    gate.get_by_role("button", name="discard").click()
    expect(ui_page.locator("[data-recipe]")).to_have_count(0)
    inbox = ui.routines / "uir" / "inbox"
    until(lambda: list(inbox.glob("msg-pending-*.json")), what="the routine was told")
    text = read_json(next(inbox.glob("msg-pending-*.json")))["text"]
    assert "already made this change himself" in text
    assert "was not wanted" not in text


def test_several_revisions_are_all_listed(ui, ui_page):
    _queue(ui, lines=(LINE, "main.md — drop the duplicate recheck step"))
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-recipe]")
    expect(card).to_contain_text("proposes 2 revisions to its own recipe")
    expect(card.locator("[data-recipe-line]")).to_have_count(2)
