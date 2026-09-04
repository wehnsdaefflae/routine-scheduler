"""Deferrals whose carrier closed without delivering them (the F324 loss mechanism).

The fixture is the real history, because it is the best possible test: on 2026-08-21 the
stopping-conditions SIDEBAR PANEL was deferred into F324; on 2026-08-26 F324 shipped the rail
and closed `addressed`, naming R339/R340/R341/F336 as delivered — F336 had been deferred into it
the same way and DID land, the panel did not. Nothing in the ledger could tell those two apart,
which is why the panel was invisible for six days and only surfaced when the user asked.
"""

from __future__ import annotations

from rsched.readmodels import orphans

# --- the real rows, trimmed to what the check reads -----------------------------------------

F324 = {
    "id": "F324", "status": "addressed",
    "title": "Run-view sidebar cluster (collapsible panels, artifacts) — carries D98's panel",
    "detail": ("The shared collapsible-panel component closes R339/R340/R341 AND hosts the "
               "stopping-conditions panel deferred from 0.208.0. [20260826] ADDRESSED 0.230.0: "
               "new static/components/rail.js renders BOTH the run and conversation views. "
               "Closes R339/R340/R341/F336."),
}
F336 = {
    "id": "F336", "status": "addressed",
    "title": "artifact registration",
    "detail": ("a run needs a way to REGISTER a produced file as a chat-visible artifact. "
               "Ships with the F324 sidebar increment. [20260826] ADDRESSED 0.230.0."),
}
DEFER_ROW = {
    "ts": "2026-08-21T01:20:00+02:00", "commit": "33b2c3a6", "items": ["D98", "F334"],
    "summary": ("0.208.0: semantic stopping conditions v1 (D98=A, F334) — engine/stopping.py "
                "store, state-digest section, finish gate. Sidebar panel deliberately deferred "
                "to the F324 shared-component build (next increment)."),
}
CLOSE_ROW = {
    "ts": "2026-08-26T22:00:00+02:00", "commit": "71cbe817", "items": ["F324"],
    "summary": ("0.230.0: new static/components/rail.js renders BOTH views; every section "
                "collapses. Closes R339/R340/R341/F336."),
}


def test_it_finds_the_panel_that_f324_closed_without():
    got = orphans.find([F324, F336], [DEFER_ROW, CLOSE_ROW])
    assert len(got) == 1
    row = got[0]
    assert row["carrier"] == "F324" and row["carrier_status"] == "addressed"
    assert row["source_ids"] == ["D98", "F334"]
    assert row["source"] == "changelog 33b2c3a6"
    # the promise itself travels, so a reader judges the actual sentence rather than trusting
    # this module's opinion of it
    assert "Sidebar panel deliberately deferred" in row["promise"]


def test_f336_reads_as_delivered_because_the_closure_names_it():
    """The distinction the ledger could not draw by eye: F336 was deferred into F324 the same
    way, and F324's closure names it."""
    got = orphans.find([F324, F336], [DEFER_ROW, CLOSE_ROW])
    assert all("F336" not in o["source_ids"] for o in got)
    assert all(o["source"] != "finding F336" for o in got)


def test_an_open_carrier_is_not_an_orphan():
    """The deferral is still tracked BY the carrier — that is the system working."""
    assert orphans.find([{**F324, "status": "in_progress"}, F336],
                        [DEFER_ROW, CLOSE_ROW]) == []


def test_a_missing_carrier_is_not_flagged():
    """An id the report no longer carries (archived out) cannot be judged either way, and
    guessing would make every old row noise."""
    assert orphans.find([F336], [DEFER_ROW]) == []


def test_a_carrier_that_names_the_source_in_its_own_detail_counts_as_delivered():
    carrier = {"id": "F400", "status": "addressed",
               "detail": "shipped it, including the F401 piece deferred here"}
    row = {"ts": "t", "commit": "abc", "items": ["F401"],
           "summary": "the panel is deferred to F400"}
    assert orphans.find([carrier], [row]) == []


def test_an_item_never_flags_itself():
    """A closure note that names its own id ("F324 … deferred to F324") is a wording artefact,
    not a deferral."""
    f = {"id": "F500", "status": "addressed", "detail": "the rest is deferred to F500 later"}
    assert orphans.find([f], []) == []


def test_every_documented_phrasing_is_detected():
    """The vocabulary is the check's whole reach — a phrasing missing from it is a deferral
    this cannot see, so each one that exists in the ledger is pinned."""
    carrier = {"id": "F900", "status": "addressed", "detail": "shipped something else"}
    for phrasing in ("the panel ships with F900",
                     "the panel rides F900",
                     "the panel rides on F900",
                     "the panel is deferred to F900",
                     "the panel folded into F900",
                     "the panel carried into F900",
                     "the panel moves to F900"):
        row = {"ts": "t", "commit": "c", "items": ["F901"], "summary": phrasing}
        assert orphans.find([carrier], [row]), f"missed: {phrasing!r}"


def test_load_reads_the_audit_dir_and_survives_a_missing_one(tmp_path):
    import json

    assert orphans.load(tmp_path) == []            # no audit dir at all
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "report.json").write_text(json.dumps({"findings": [F324, F336]}), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(
        json.dumps(DEFER_ROW) + "\n" + json.dumps(CLOSE_ROW) + "\n", encoding="utf-8")
    got = orphans.load(tmp_path)
    assert [o["carrier"] for o in got] == ["F324"]


def test_the_api_surfaces_it(api_client, make_routine):
    import json

    c, _tmp = api_client
    d = make_routine(slug="self-audit")
    audit = d / "audit"
    audit.mkdir()
    (audit / "report.json").write_text(json.dumps({"findings": [F324, F336]}), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(
        json.dumps(DEFER_ROW) + "\n" + json.dumps(CLOSE_ROW) + "\n", encoding="utf-8")
    got = c.get("/api/items/orphans").json()
    assert len(got) == 1 and got[0]["carrier"] == "F324"
    assert got[0]["source_ids"] == ["D98", "F334"]


# --- the second loss mechanism: an addressed report whose message was never written ----------


def test_an_addressed_report_with_no_message_in_the_inbox_is_surfaced(tmp_path):
    """D114: `file_report` writes the ledger row and the target's inbox message in one call, so
    an addressed report always has a message waiting. A row appended any OTHER way — an operator
    batch written straight to the stream — has a target and no message, so the target can never
    drain it and it counts as open forever. Twelve rows from the 2026-08-29 migration are that.
    """
    from rsched.readmodels.orphans import find_undelivered

    for slug in ("radar", "improver"):
        (tmp_path / slug / "inbox").mkdir(parents=True)
        (tmp_path / slug / "routine.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    # delivered normally: the message is sitting in the inbox, waiting for the next run
    (tmp_path / "radar" / "inbox" / "msg-rep-R1.json").write_text("{}", encoding="utf-8")

    rows = [
        {"id": "R1", "target": "radar", "routine": "operator", "ts": "2026-08-29T10:00:00+02:00",
         "title": "waiting normally"},
        {"id": "R2", "target": "improver", "routine": "operator", "ts": "2026-08-29T11:00:00+02:00",
         "title": "batch-appended, never delivered"},
        {"id": "R3", "target": "gone", "routine": "operator", "ts": "2026-08-29T12:00:00+02:00",
         "title": "addressed to a routine that does not exist"},
        {"id": "R4", "target": "improver", "routine": "self-audit", "ts": "2026-08-30T09:00:00+02:00",
         "title": "already read", "delivered": {"ts": "…", "run_id": "improver:x"}},
        {"id": "R5", "target": "improver", "routine": "self-audit", "ts": "2026-08-30T10:00:00+02:00",
         "title": "withdrawn on purpose", "retracted": {"ts": "…"}},
        {"id": "R6", "routine": "self-audit", "ts": "2026-08-30T11:00:00+02:00",
         "title": "unaddressed — there is no delivery to miss"},
    ]
    found = find_undelivered(rows, tmp_path)
    assert [o["id"] for o in found] == ["R3", "R2"]          # newest first
    assert found[0]["target_exists"] is False and found[1]["target_exists"] is True
    assert all(o["kind"] == "undelivered" for o in found)


def test_an_undelivered_closure_is_not_a_lost_report(tmp_path):
    """A closure (answers + closes) is born settled — the terminal acknowledgment of an
    exchange, asking nothing back. An operator closure written straight to the stream
    (R1152-R1156, 2026-09-04) lacks an inbox file like any batch-appended row, but being
    settled is its whole point: there is nothing for the target to act on, so it is NOT lost
    work and must not sit in the 'addressed, never delivered' banner."""
    from rsched.readmodels.orphans import find_undelivered

    (tmp_path / "radar" / "inbox").mkdir(parents=True)
    (tmp_path / "radar" / "routine.yaml").write_text("slug: radar\n", encoding="utf-8")
    rows = [
        {"id": "R10", "target": "radar", "routine": "operator", "closes": True, "answers": "R9",
         "ts": "2026-09-04T10:00:00+02:00", "title": "Closed: your exposure is shut"},
        {"id": "R11", "target": "radar", "routine": "operator",
         "ts": "2026-09-04T11:00:00+02:00", "title": "a real undelivered work order"},
    ]
    found = find_undelivered(rows, tmp_path)
    assert [o["id"] for o in found] == ["R11"]        # the closure excluded, the work order kept
