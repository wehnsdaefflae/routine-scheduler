"""The age line on a Messages card, in a real browser (F753).

The server's claim is tested in tests/test_item_first_seen.py; this is the operator's half:
the page LOADS, and what it shows about an item's age is readable and honestly qualified.

Two readings have to be distinguishable on screen, because the whole finding is about false
precision in a date:

* a floor — the item already existed when stamping began, so no true first sight exists and
  the card says "or more" / "or earlier" rather than a bare date;
* an exact sighting — the instance watched the item arrive and the date is a real one.

The REPORT carries its genuine filing time from `.control/reports.jsonl`, which nothing
rewrites, so its card reads exactly.
"""

import json

from playwright.sync_api import expect

REPORT = {
    "schema": 1, "run_id": "self-audit:20260716-200000",
    "generated": "2026-07-16T20:00:00+00:00",
    "since": {"commit": "abc1234f", "window": "2 runs"},
    "summary": "F1 is carried this run.",
    "findings": [{"id": "F1", "severity": "problem", "status": "open",
                  "title": "The thing is broken", "detail": "Blocked on D1."}],
    "decisions": [],
}

#: A changelog row naming F1, dated well before any stamping could have begun: the floor the
#: card must show for a pre-existing item (evidence it existed then, not a birth date).
CHANGELOG = ('{"ts": "2026-07-15T09:00:00+00:00", "commit": "aaaa1111", '
             '"run_id": "self-audit:1", "items": ["F1"], '
             '"summary": "0.90.0 \\u2014 F1 was addressed once"}\n')

BUG = {"id": "R1", "ts": "2026-07-14T08:00:00+00:00", "routine": "uir",
       "run_id": "uir:20260714-070000", "title": "A util blew up under the sandbox",
       "detail": "exit 2 creating its output dir"}


def _seed(ui, make_routine):
    make_routine(slug="self-audit")
    audit = ui.routines / "self-audit" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(CHANGELOG, encoding="utf-8")
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "reports.jsonl").write_text(json.dumps(BUG) + "\n", encoding="utf-8")


def test_a_card_shows_the_items_age_and_qualifies_a_floor(ui, ui_page, make_routine):
    """F753: every finding and decision was stamped with the report's REWRITE date, so item
    age could not be read on this page at all. Each card now carries an age line — and for an
    item that predates stamping it says so, in words, instead of showing a date that would be
    read as a birthday."""
    _seed(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/messages?status=all&type=all")
    ui_page.wait_for_selector("#ref-F1")

    age = ui_page.locator("#ref-F1 [data-first-seen]")
    expect(age).to_be_visible()
    expect(age).to_contain_text("age")
    # a pre-existing item: qualified on BOTH halves, the age and the date
    expect(age).to_contain_text("or more")
    expect(age).to_contain_text("or earlier")
    expect(age).to_have_attribute("data-first-seen-floor", "1")
    # the floor is the changelog trace that PROVES F1 existed in July, not the epoch
    expect(age).to_contain_text("2026-07-15")
    # the hover text spells out what a floor means, for a reader who wonders why it hedges
    assert "true first sight is unknown" in (age.get_attribute("title") or "")


def test_a_reports_card_shows_its_real_filing_age(ui, ui_page, make_routine):
    """A report's filing time is in the ledger and is never rewritten, so its card reads as an
    exact sighting — no "or earlier" hedge where a true date exists."""
    _seed(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/messages?status=all&type=all")
    ui_page.wait_for_selector("#ref-R1")

    age = ui_page.locator("#ref-R1 [data-first-seen]")
    expect(age).to_be_visible()
    expect(age).to_have_attribute("data-first-seen-floor", "0")
    expect(age).to_contain_text("2026-07-14")
    assert "or earlier" not in (age.text_content() or "")
    assert "or more" not in (age.text_content() or "")
