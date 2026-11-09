"""The Decisions page splits by CLASS, not by whose process is parked — D212 increment (c).

The operator's answer, 2026-10-25: *"Both axes: subject decides the default, the run may
override it when the work is genuinely blocked"*. Increment (a) put that class on every record
(`engine/askclass.py`): `blocking` means the run could not do the work it was fired to do
without the answer, `closing` that it finished and the answer shapes the next run. The two are
ORTHOGONAL to `mode`, which says only whether the asking run's PROCESS sat and waited.

This page grouped by `mode`. Measured over the 282 records the instance had filed, `mode:
"blocking"` was used 0 times — so the band that promised "a run is waiting on you" was
structurally empty, while a run that said `blocks_work: true` sat in "Deferred" among the asks
nothing is held up by. That is the thing he asked to see at a glance.

What a person SEES, and what is asserted here:

* a `blocking`-class ask lands in the Blocking band even when the run that filed it has ENDED —
  the work is still blocked, which is what the class means (before this it was demoted to
  Proposals, the band whose own caption says nobody is waiting);
* a `closing`-class ask lands in the Closing band even while its run is alive, because nothing
  about it stopped that run;
* an ACCESS REQUEST with no `class` key — every record filed before increment (a) — reads as
  blocking by its subject, which is `askclass.of`'s own default;
* the filter chips count the same two buckets, so the toolbar and the bands cannot disagree.

Every assertion about an element first asserts it is PRESENT (`to_have_count(1)`).
"""

from __future__ import annotations

from playwright.sync_api import expect

HEADS = ".q-group-head"


def test_a_blocking_class_ask_bands_as_blocking_though_its_run_ended(ui, ui_page):
    """The point of the increment. `blocks_work: true` is the run saying it could not do its
    job without this; the run then ended. Nothing is parked, and the work is still blocked.
    """
    ui.seed_question("uir", "q-20260714-070000-1", "May I drop the old column?",
                     asked="20260714-070000", extra={"class": "blocking"})

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Blocking")
    expect(heads.nth(0)).to_contain_text("could not do its job")
    card = ui_page.locator(".question-item", has_text="old column")
    expect(card).to_have_count(1)


def test_a_closing_class_ask_bands_as_closing_though_its_run_is_alive(ui, ui_page):
    """The other direction: the run is still going, and this question did not stop it. It
    belongs with what merely waits, never with what is holding work up.
    """
    ui.seed_run("uir", "20260714-080000", "running")
    ui.seed_question("uir", "q-20260714-080000-2", "Keep polling hourly?",
                     asked="20260714-080000", extra={"class": "closing"})

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Closing")
    expect(heads.nth(0)).to_contain_text("shapes the next")
    card = ui_page.locator(".question-item", has_text="polling hourly")
    expect(card).to_have_count(1)


def test_both_classes_at_once_render_blocking_first(ui, ui_page):
    """One band each, and the one holding work up on top — the glance the split exists for.

    The closing ask is seeded against a LIVE run deliberately: a closing ask whose filing run
    has ended is a PROPOSAL (R2425 — nothing consumes the answer on a clock), and that band is
    unchanged by this increment. A BLOCKING one is never demoted that way, which is the
    asymmetry the split is for.
    """
    ui.seed_question("uir", "q-20260714-070000-3", "May I drop the old column?",
                     asked="20260714-070000", extra={"class": "blocking"})
    ui.seed_run("uir", "20260714-080000", "running")
    ui.seed_question("uir", "q-20260714-080000-4", "Keep polling hourly?",
                     asked="20260714-080000", extra={"class": "closing"})

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(2)
    expect(heads.nth(0)).to_contain_text("Blocking")
    expect(heads.nth(1)).to_contain_text("Closing")


def test_an_access_request_without_a_class_key_reads_as_blocking(ui, ui_page):
    """`askclass.of`'s subject default, which every record filed before increment (a) relies
    on: the run asked for the entity because something it meant to do needs it.
    """
    ui.seed_question("uir", "q-20260714-070000-5", "May I write to the share?",
                     asked="20260714-070000", request=["fs-write:/srv/share"])

    ui_page.goto(f"{ui.url}/#/questions")
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Blocking")


def test_the_filter_chips_count_the_same_two_buckets(ui, ui_page):
    """The toolbar reads from the same derivation as the bands, so a chip can never offer a
    filter the page then renders under another name.
    """
    ui.seed_question("uir", "q-20260714-070000-6", "May I drop the old column?",
                     asked="20260714-070000", extra={"class": "blocking"})
    ui.seed_run("uir", "20260714-080000", "running")
    ui.seed_question("uir", "q-20260714-080000-7", "Keep polling hourly?",
                     asked="20260714-080000", extra={"class": "closing"})

    ui_page.goto(f"{ui.url}/#/questions")
    blocking = ui_page.locator(".toolbar button", has_text="Blocking")
    closing = ui_page.locator(".toolbar button", has_text="Closing")
    expect(blocking).to_have_count(1)
    expect(closing).to_have_count(1)
    expect(blocking).to_contain_text("· 1")
    expect(closing).to_contain_text("· 1")
    # and the chip really filters: picking Closing leaves the one closing band
    closing.click()
    heads = ui_page.locator(HEADS)
    expect(heads).to_have_count(1)
    expect(heads.nth(0)).to_contain_text("Closing")
