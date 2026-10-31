"""The Messages page's ORPHAN BANNERS — the three ways work leaves the ledger without becoming an
open item anywhere (`readmodels/orphans.py`, `readmodels/prose_closures.py`), shown above the list
because they are invisible to every filter below it.

Apart from `test_messages_page.py` (the list, its filters, its composer) because the banner is a
different surface with a different source: `GET /api/items/orphans`, loaded by its own module
(`static/components/orphan-banner.js`) and judged against the fleet rather than against the item
index.
"""

import json

from playwright.sync_api import expect


def _seed(ui, make_routine, rows):
    """A real self-audit routine (the page resolves the slug through the registry) and the
    report stream the banner reads."""
    make_routine(slug="self-audit")
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    with (control / "reports.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_messages_discards_an_undelivered_orphan(ui, ui_page, make_routine):
    """F435 (operator 2026-09-04): a report addressed to a routine but written straight to the
    stream (no inbox message) can never be delivered, so it sits in the 'addressed, never
    delivered' banner forever. The banner now carries a per-row DISCARD that marks the row dropped
    (a retracted event), off the banner and out of the backlog."""
    make_routine(slug="routine-improver")
    _seed(ui, make_routine, [
        {"id": "R2", "ts": "2026-09-04T10:00:00+02:00", "routine": "operator",
         "target": "routine-improver", "title": "batch-appended, never delivered"}])
    control = ui.routines / ".control"
    ui_page.goto(f"{ui.url}/#/messages?status=all&type=all")
    ui_page.wait_for_selector("h1:has-text('Messages')")

    expect(ui_page.locator(".q-group-head", has_text="addressed, never delivered")).to_be_visible()
    discard = ui_page.locator("button", has_text="discard")      # unique to the undelivered banner
    expect(discard).to_have_count(1)
    discard.click()

    # the banner empties (its only orphan is gone)…
    expect(ui_page.locator("button", has_text="discard")).to_have_count(0)
    expect(ui_page.locator(".q-group-head", has_text="addressed, never delivered")).to_have_count(0)
    # …because a `retracted` event was appended to the ledger, so R2 now reads dropped
    rows = [json.loads(x) for x in (control / "reports.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert any(r.get("id") == "R2" and r.get("event") == "retracted" for r in rows)


def test_the_banner_says_which_undelivered_targets_are_still_on_the_instance(
        ui, ui_page, make_routine):
    """F768: the banner's sentence turns on the SAME fleet fact as this page's `unroutable`
    count — `registry.scan`'s keys — so the two cannot say opposite things about one row. An
    installed target reads "whose inbox holds no message for it" (someone could still be given
    it); a target the instance does not hold reads "which is not a routine on this instance".
    """
    make_routine(slug="routine-improver")
    _seed(ui, make_routine, [
        {"id": "R2", "ts": "2026-09-04T10:00:00+02:00", "routine": "operator",
         "target": "routine-improver", "title": "never delivered to routine-improver"},
        {"id": "R3", "ts": "2026-09-04T11:00:00+02:00", "routine": "operator",
         "target": "grants-radar", "title": "never delivered to grants-radar"}])
    ui_page.goto(f"{ui.url}/#/messages?status=all&type=all")
    ui_page.wait_for_selector("h1:has-text('Messages')")

    expect(ui_page.locator(".q-group-head", has_text="addressed, never delivered")).to_be_visible()
    installed = ui_page.locator(".card", has_text="never delivered to routine-improver")
    expect(installed).to_contain_text("whose inbox holds no message for it")
    gone = ui_page.locator(".card", has_text="never delivered to grants-radar")
    expect(gone).to_contain_text("which is not a routine on this instance")


def test_the_banner_shows_a_row_a_settled_reports_prose_claimed_to_close(
        ui, ui_page, make_routine):
    """F793, the third loss mechanism: R2886 said "Closing R2313" in its opening words, carried
    `answers: R2743` + `closes: true` and no `settles`, and R2313 stayed the oldest live row on
    the instance for 20 days. The F770 check says so ONCE, on the filing observation, to the one
    party who already believes the row is closed — so the banner is its second reader, and it
    names the OWNER, the only routine whose `settles` can end the row.
    """
    make_routine(slug="scheduler-improvement-research")
    _seed(ui, make_routine, [
        {"id": "R2313", "ts": "2026-10-07T18:10:00+02:00", "routine": "c-20261007-150942",
         "target": "scheduler-improvement-research", "detail": "Two findings, filed together.",
         "title": "create_routine cannot REVISE an existing routine",
         "delivered": {"ts": "2026-10-10T09:00:00+02:00", "run_id": "x:20261010-010003"}},
        {"id": "R2886", "ts": "2026-10-24T03:03:10+02:00", "closes": True, "answers": "R2743",
         "routine": "scheduler-improvement-research", "target": "self-audit",
         "title": "R2313 settled: finding 1 is answered by D198 and parked",
         "detail": "Closing R2313. Both halves disposed of, so nothing should be re-derived."}])
    ui_page.goto(f"{ui.url}/#/messages?status=all&type=all")
    ui_page.wait_for_selector("h1:has-text('Messages')")

    expect(ui_page.locator(".q-group-head",
                           has_text="claimed closed in prose, still open")).to_be_visible()
    card = ui_page.locator(".card", has_text="create_routine cannot REVISE an existing routine")
    expect(card).to_contain_text("R2886")                       # the report that claimed it
    expect(card).to_contain_text("scheduler-improvement-research")   # who can still settle it
    # the claiming words themselves, quoted from the text that was JUDGED. Both of R2886's texts
    # claim the row ("R2313 settled: …" in the title, "Closing R2313." in the detail's opening
    # sentence) and the title wins, because that is the prominent statement a reader is weighing.
    expect(card).to_contain_text("claimed by R2886: “R2313 settled: finding 1 is answered")
