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

from rsched.paths import atomic_write_json

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
    ui_page.get_by_role("button", name="discard").last.click()      # the confirm dialog
    expect(ui_page.locator("#toast")).to_contain_text("the proposing routine was told")
    expect(ui_page.locator("[data-recipe]")).to_have_count(0)


def test_several_revisions_are_all_listed(ui, ui_page):
    _queue(ui, lines=(LINE, "main.md — drop the duplicate recheck step"))
    ui.seed_run("uir", "20261025-140000", "finished")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-recipe]")
    expect(card).to_contain_text("proposes 2 revisions to its own recipe")
    expect(card.locator("[data-recipe-line]")).to_have_count(2)
