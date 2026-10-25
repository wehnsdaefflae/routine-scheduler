"""The Messages page's ORPHAN BANNERS — the two ways work leaves the ledger without becoming an
open item anywhere (`readmodels/orphans.py`), shown above the list because they are invisible to
every filter below it.

Apart from `test_messages_page.py` (the list, its filters, its composer) because the banner is a
different surface with a different source: `GET /api/items/orphans`, loaded by its own
`loadOrphans()` and judged against the fleet rather than against the item index.
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
