"""The item ORIGIN STAMP (F753): how old is an item, and can the index be asked for the
ten oldest?

Every finding and decision on the Messages page was stamped with `origin.ts` — the
`generated` date of the report that CONTAINS it. `self-audit` rewrites `audit/report.json`
wholesale every run, so that date moved to "today" on every run and an item's age was
unreadable: self-audit measured its own /api/items rows as carrying no date field of any
kind and had to fall back to the id ordinal to pick a quota.

So the index carries `first_seen` (plus `first_seen_or_earlier`) from a store the rewrite
cannot reach — `rsched.firstseen`, one small JSON map under `.control/` beside the ⚑ flags
(the same reasoning as `priorities.py`: report.json is rewritten wholesale and is not ours
to write, and reports.jsonl records what runs SAID, not UI state).

These tests pin the four claims the feature stands on:

1. every row carries a stamp, and a REPORT's is its real filing time from the ledger;
2. the stamp survives a wholesale rewrite of report.json that moves `origin.ts`;
3. an item that appears AFTER stamping began reads as an exact first sighting, while one
   that was already there reads as a floor ("or earlier") — never a fabricated birth date;
4. the stamp is served fresh rather than baked into the memoized index.
"""

from __future__ import annotations

import json

import pytest

from rsched import firstseen
from rsched.readmodels import memo

REPORT = {
    "schema": 1, "run_id": "self-audit:20260725-000002",
    "generated": "2026-07-25T00:38:00+00:00",
    "since": {"commit": "5854843551", "window": "1 routine run"},
    "summary": "F1 carried; D1 awaits you.",
    "findings": [{"id": "F1", "severity": "problem", "title": "An old finding",
                  "detail": "It has been here a while.", "status": "open"}],
    "decisions": [{"id": "D1", "title": "Pick a path", "detail": "See F1.",
                   "options": ["do it", "leave it"], "status": "open"}],
}

#: One row naming F1 explicitly, dated long before any stamping could have begun: it PROVES
#: F1 existed in July, which is a better floor for a pre-existing item than the epoch.
CHANGELOG = ('{"ts": "2026-07-22T10:00:00+00:00", "commit": "cccc3333", '
             '"run_id": "self-audit:3", "items": ["F1"], '
             '"summary": "0.92.0 \\u2014 F1 was addressed once"}\n')

#: A report carries its own filing time in the ledger — the gap F753 names is findings and
#: decisions, so this row must keep reading its genuine `ts` rather than a store stamp.
BUG_TS = "2026-07-19T21:20:06+02:00"
BUG = {"id": "R1", "ts": BUG_TS, "routine": "routine-improver",
       "run_id": "routine-improver:20260719-190013", "title": "write_file clobbered a file",
       "detail": "append:true overwrote"}


@pytest.fixture
def seeded(api_client):
    """`(client, routines_home)` — an API client over a home holding all three item types."""
    c, tmp = api_client
    home = tmp / "routines"
    audit = home / "self-audit" / "audit"
    audit.mkdir(parents=True)
    (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(CHANGELOG, encoding="utf-8")
    control = home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "reports.jsonl").write_text(json.dumps(BUG) + "\n", encoding="utf-8")
    memo.reset()
    return c, home


def _rows(client):
    # bare `/api/items` is the UNFILTERED index: `status` is a literal comma-list of the
    # vocabulary (`status=all` matches nothing), so an empty param is how you ask for everything
    return {i["id"]: i for i in client.get("/api/items").json()["items"]}


def test_every_item_row_carries_a_first_seen_stamp(seeded):
    """The field F753 is about: an age that can be computed at all. Not a single /api/items
    row carried a date of first sight before this — only `origin.ts`, the rewrite date."""
    client, _ = seeded
    rows = _rows(client)
    assert set(rows) >= {"F1", "D1", "R1"}
    for item_id in ("F1", "D1", "R1"):
        assert rows[item_id]["first_seen"], f"{item_id} carries no first_seen stamp"
        assert "first_seen_or_earlier" in rows[item_id]


def test_a_report_keeps_its_real_filing_time_from_the_ledger(seeded):
    """`.control/reports.jsonl` already records when a report was filed, and that record is
    never rewritten — so an `R<n>` reads its own `ts` exactly, and no second stamp is
    invented for it."""
    client, _ = seeded
    r1 = _rows(client)["R1"]
    assert r1["first_seen"] == BUG_TS
    assert r1["first_seen_or_earlier"] is False     # a filing time, not a floor


def test_the_stamp_survives_a_wholesale_rewrite_of_report_json(seeded):
    """THE regression test for F753. `self-audit` rewrites report.json every run: the same
    finding comes back under a new `generated` date, which is what `origin.ts` reads. The
    first-seen stamp must not move with it."""
    client, home = seeded
    before = _rows(client)["F1"]
    rewritten = {**REPORT, "run_id": "self-audit:20261028-140000",
                 "generated": "2026-10-28T14:00:00+00:00"}
    (home / "self-audit" / "audit" / "report.json").write_text(
        json.dumps(rewritten), encoding="utf-8")
    after = _rows(client)["F1"]
    assert after["origin"]["ts"] == "2026-10-28T14:00:00+00:00"   # the rewrite DID move this
    assert after["first_seen"] == before["first_seen"]            # F753: the stamp did not
    assert after["first_seen_or_earlier"] == before["first_seen_or_earlier"]


def test_an_item_seen_after_stamping_began_reads_as_an_exact_first_sighting(seeded):
    """What makes "the ten oldest" answerable: an item that turns up later carries a TRUE
    first-sight date (`first_seen_or_earlier` false), while one that was already in the
    report when stamping began carries a floor — never a fabricated birth date."""
    client, home = seeded
    assert _rows(client)["F1"]["first_seen_or_earlier"] is True      # already there: a floor
    audit = home / "self-audit" / "audit"
    grown = {**REPORT, "findings": [*REPORT["findings"],
                                    {"id": "F900", "severity": "info", "status": "open",
                                     "title": "A finding raised after stamping began",
                                     "detail": ""}]}
    (audit / "report.json").write_text(json.dumps(grown), encoding="utf-8")
    rows = _rows(client)
    assert rows["F900"]["first_seen_or_earlier"] is False            # a real first sighting
    # and the newcomer IS younger than the pre-existing item's floor, which is what makes the
    # two orderable at all — the point of the field
    assert rows["F900"]["first_seen"] > rows["F1"]["first_seen"]


def test_a_pre_existing_items_floor_uses_its_earliest_changelog_trace(seeded):
    """A pre-existing item has no true first-sight date, and every one of them would
    otherwise share the epoch — which leaves "the ten oldest" unmeasurable among exactly the
    items that are oldest. A changelog row naming the id PROVES it existed then, so the
    earliest such row is the floor when it is older than the epoch."""
    client, _ = seeded
    f1 = _rows(client)["F1"]
    assert f1["first_seen"] == "2026-07-22T10:00:00+00:00"   # the row that named F1
    assert f1["first_seen_or_earlier"] is True               # evidence of a floor, not a sighting


def test_the_stamp_is_served_fresh_rather_than_from_the_memoized_index(seeded):
    """The index is memoized behind the stat fingerprint of its source files; the store is
    NOT one of them (it is written while serving, which would invalidate the memo on every
    new item). So the stamps are read per request — proven by removing the store under a
    warm memo: the next answer re-stamps instead of serving rows with no date."""
    client, home = seeded
    assert _rows(client)["F1"]["first_seen"]
    store = home / ".control" / "item-first-seen.json"
    assert store.is_file(), "serving the index did not write the store"
    store.unlink()
    again = _rows(client)                       # every memoized source is unchanged
    assert again["F1"]["first_seen"], "a warm request served a row with no stamp"

# ---- the store itself: what it accepts, and what it will never move ----------------------


def test_a_stamp_is_never_moved_by_a_later_pass(tmp_path):
    """The one guarantee the whole feature rests on. The serve path stamps on EVERY request,
    so the second pass must be a no-op for an id already recorded — a store that re-stamped
    would read "first seen today" forever, which is the bug F753 describes."""
    home = tmp_path / "routines"
    firstseen.stamp(home, ["F1"])
    first = firstseen.read_first_seen(home)["items"]["F1"]
    firstseen.stamp(home, ["F1", "F2"])                  # F1 again, plus a newcomer
    store = firstseen.read_first_seen(home)
    assert store["items"]["F1"] == first                 # untouched
    assert store["items"]["F2"]                          # the newcomer got its own
    assert store["epoch"] == first                       # the epoch is set once, by pass one


def test_only_item_ids_reach_the_store(tmp_path):
    """`priorities.ITEM_ID_RE` is the one definition of an item id, and this store uses it:
    anything else is filtered out rather than stored as a junk key. A run SUMMARY's id is a
    RUN id (`<slug>:<ts>`) by design, and the serve path passes the whole index — so dropping
    a non-item id silently is the correct behaviour here, not a refusal."""
    home = tmp_path / "routines"
    firstseen.stamp(home, ["F1", "X99", "", "self-audit:20261028-140001", "d7", "r10000"])
    assert set(firstseen.read_first_seen(home)["items"]) == {"F1", "D7", "R10000"}


def test_a_malformed_store_on_disk_reads_as_empty_rather_than_crashing(tmp_path):
    """Junk in the file must not take the Messages page down with it, and a key that is not
    an item id must not survive a read even if something wrote one."""
    home = tmp_path / "routines"
    control = home / ".control"
    control.mkdir(parents=True)
    (control / "item-first-seen.json").write_text("not json at all", encoding="utf-8")
    assert firstseen.read_first_seen(home) == {"epoch": "", "items": {}, "backfilled": set()}
    (control / "item-first-seen.json").write_text(
        json.dumps({"epoch": "2026-10-28T14:00:00+02:00",
                    "items": {"F1": "2026-10-28T14:00:00+02:00", "nonsense": "x",
                              "D2": 17}}), encoding="utf-8")
    store = firstseen.read_first_seen(home)
    assert set(store["items"]) == {"F1"}                 # the junk key and the non-date are gone


def test_an_unstamped_row_with_no_trace_reads_as_a_floor_not_as_new(tmp_path):
    """The backfill honesty rule, at the unit level: asked about an item it holds nothing for,
    the store answers with the epoch AS A FLOOR — never with "today, exactly"."""
    home = tmp_path / "routines"
    firstseen.stamp(home, ["F1"])
    store = firstseen.read_first_seen(home)
    stamp, floor = firstseen.row_stamp({"id": "F404", "origin": {"ts": ""}}, store)
    assert stamp == store["epoch"] and floor is True
