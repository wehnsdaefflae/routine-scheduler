"""The Items read model (`readmodels/items.py`) and its API: the merge of report.json,
changelog.jsonl, decisions-answered.json and reports.jsonl into one indexed shape
(docs/items.md), and the GET /api/items filters.
"""

from __future__ import annotations

import json

import pytest

from rsched import priorities
from rsched.readmodels import items as items_model
from rsched.readmodels import memo

REPORT = {
    "schema": 1, "run_id": "self-audit:20260725-000002",
    "generated": "2026-07-25T00:38:00+00:00",
    "since": {"commit": "5854843551", "window": "1 routine run"},
    "summary": "F1 carried; D1 awaits you.",
    "findings": [
        {"id": "F1", "severity": "problem", "title": "A finding with no status on disk",
         "detail": "Blocked on D1.", "evidence": ["src/rsched/engine/loop.py"]},
        {"id": "F2", "severity": "info", "title": "A finding the routine marked open",
         "status": "open", "detail": "still there"},
    ],
    "decisions": [
        {"id": "D1", "title": "Pick a path", "detail": "See F1.",
         "options": ["do it", "leave it"], "status": "open"},
        {"id": "D2", "title": "Already decided", "detail": "done",
         "status": "settled", "resolution": "did it"},
        {"id": "D3", "title": "Answered by the user", "detail": "-", "options": ["a", "b"]},
    ],
}

# The real file mixes pretty-printed and compact objects — a line-oriented parser drops the
# multi-line rows entirely, which is why read_changelog streams with raw_decode.
CHANGELOG = """{"ts": "2026-07-20T10:00:00+00:00", "commit": "aaaa1111", "run_id": "self-audit:1", \
"summary": "0.90.0 \\u2014 F9 fixed the thing"}
{
  "ts": "2026-07-21T10:00:00+00:00",
  "commit": "bbbb2222",
  "run_id": "self-audit:2",
  "title": "pretty-printed row",
  "summary": "0.91.0 \\u2014 D9 settled, F9 revisited"
}
{"ts": "2026-07-22T10:00:00+00:00", "commit": "cccc3333", "run_id": "self-audit:3", \
"items": ["F1", "R2"], "summary": "0.92.0 \\u2014 explicit item links"}
{"ts": "2026-07-23T10:00:00+00:00", "commit": "dddd4444", "run_id": "self-audit:4", \
"summary": "0.93.0 \\u2014 a change naming no item at all"}
"""

BUGS = [
    {"id": "R1", "ts": "2026-07-19T21:20:06+02:00", "routine": "routine-improver",
     "run_id": "routine-improver:20260719-190013", "title": "write_file clobbered a file",
     "detail": "append:true overwrote"},
    {"id": "R2", "ts": "2026-07-20T08:00:00+02:00", "routine": "self-audit",
     "run_id": "self-audit:20260720-080000", "title": "a bug that got fixed", "detail": ""},
]


@pytest.fixture
def audit_home(tmp_path):
    """A routines home holding a self-audit routine with all four item sources."""
    memo.reset()
    home = tmp_path / "routines"
    audit = home / "self-audit" / "audit"
    audit.mkdir(parents=True)
    (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(CHANGELOG, encoding="utf-8")
    (audit / "decisions-answered.json").write_text(
        json.dumps({"D3": "2026-07-25T09:00:00+00:00"}), encoding="utf-8")
    control = home / ".control"
    control.mkdir(parents=True)
    (control / "reports.jsonl").write_text(
        "".join(json.dumps(b) + "\n" for b in BUGS), encoding="utf-8")
    return home


def _by_id(result):
    return {i["id"]: i for i in result["items"]}


# ---- the mixed-format changelog parse ---------------------------------------------------


def test_read_changelog_parses_mixed_compact_and_pretty_rows(audit_home):
    rows = items_model.read_changelog(audit_home / "self-audit" / "audit" / "changelog.jsonl")
    assert [r["commit"] for r in rows] == ["aaaa1111", "bbbb2222", "cccc3333", "dddd4444"]
    assert rows[1]["title"] == "pretty-printed row"       # the multi-line row survived intact


def test_read_changelog_keeps_what_parsed_when_the_tail_is_truncated(tmp_path):
    path = tmp_path / "changelog.jsonl"
    path.write_text('{"ts": "1", "commit": "a"}\n{"ts": "2", "comm', encoding="utf-8")
    assert [r["commit"] for r in items_model.read_changelog(path)] == ["a"]


def test_read_changelog_missing_file_is_empty(tmp_path):
    assert items_model.read_changelog(tmp_path / "nope.jsonl") == []


# ---- status ------------------------------------------------------------------------------


def test_absent_status_reads_unknown_and_is_never_parsed_from_prose(audit_home):
    """Findings carry no `status` on disk yet — the self-audit routine will emit one later.
    Until then the read model says `unknown`; it must NOT recover open-vs-fixed from the
    title (that is exactly the tolerant dual-convention code the house rules ban)."""
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    assert items["F1"]["status"] == "unknown"
    assert items["F2"]["status"] == "open"          # the report's own field wins when present


def test_report_status_outside_the_vocabulary_reads_unknown(audit_home):
    report = json.loads(json.dumps(REPORT))
    report["findings"][0]["status"] = "FIXED-ish"
    (audit_home / "self-audit" / "audit" / "report.json").write_text(
        json.dumps(report), encoding="utf-8")
    memo.reset()
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    assert items["F1"]["status"] == "unknown"


def test_answered_marker_settles_a_decision_without_a_report_status(audit_home):
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    assert items["D3"]["status"] == "settled"       # decisions-answered.json is a recorded fact
    assert items["D1"]["status"] == "open"          # the report still wins where it speaks
    assert items["D2"]["status"] == "settled"


def test_archive_only_items_are_addressed_and_carry_no_prose(audit_home):
    """An item the current report has moved past survives through the changelog alone: it is
    archive_only, reads `addressed`, and fabricates no title/detail."""
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    f9 = items["F9"]
    assert (f9["archive_only"], f9["status"], f9["title"], f9["detail"]) == (True, "addressed", "", "")
    assert f9["type"] == "finding" and items["D9"]["type"] == "decision"
    # origin is the EARLIEST linked row — the first trace of it
    assert f9["origin"]["commit"] == "aaaa1111"


# ---- the changelog join ------------------------------------------------------------------


def test_explicit_items_field_is_the_trusted_join(audit_home):
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    assert [a["link"] for a in items["F1"]["addressed"]] == ["explicit"]
    assert items["F1"]["addressed"][0]["commit"] == "cccc3333"
    # a bug id joins ONLY through the explicit field
    assert [a["commit"] for a in items["R2"]["addressed"]] == ["cccc3333"]
    assert items["R2"]["status"] == "addressed"
    # a filed report with no changelog row and no delivery is OPEN, not unknown: the ledger
    # records that it exists, which is a status, unlike a finding whose report omits one
    assert items["R1"]["status"] == "open" and items["R1"]["addressed"] == []


def test_prose_matches_are_flagged_best_effort_and_newest_first(audit_home):
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    rows = items["F9"]["addressed"]
    assert [a["commit"] for a in rows] == ["bbbb2222", "aaaa1111"]     # newest first
    assert {a["link"] for a in rows} == {"best-effort"}


def test_a_changelog_row_naming_no_item_links_nothing(audit_home):
    result = items_model.build(audit_home / "self-audit", audit_home)
    linked = {a["commit"] for i in result["items"] for a in i["addressed"]}
    assert "dddd4444" not in linked


# ---- shape, ordering, counts -------------------------------------------------------------


def test_item_shape_origin_evidence_and_refs(audit_home):
    items = _by_id(items_model.build(audit_home / "self-audit", audit_home))
    f1 = items["F1"]
    assert f1["type"] == "finding" and f1["severity"] == "problem"
    assert f1["evidence"] == ["src/rsched/engine/loop.py"]
    assert f1["refs"] == ["D1"]                       # "Blocked on D1." — the graph is navigable
    assert f1["origin"] == {"routine": "self-audit", "run_id": "self-audit:20260725-000002",
                            "ts": "2026-07-25T00:38:00+00:00", "commit": "5854843551"}
    r1 = items["R1"]
    assert r1["type"] == "report" and r1["origin"]["routine"] == "routine-improver"
    assert r1["origin"]["run_id"] == "routine-improver:20260719-190013"
    assert items["D1"]["options"] == ["do it", "leave it"]
    assert items["D2"]["resolution"] == "did it"


def test_counts_cover_every_type_and_status(audit_home):
    result = items_model.build(audit_home / "self-audit", audit_home)
    assert result["counts"]["type"] == {"finding": 3, "decision": 4, "report": 2}
    assert sum(result["counts"]["status"].values()) == len(result["items"])


def test_items_are_ordered_newest_origin_first(audit_home):
    result = items_model.build(audit_home / "self-audit", audit_home)
    stamps = [i["origin"]["ts"] for i in result["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_build_is_memoized_until_a_source_changes(audit_home):
    first = items_model.build(audit_home / "self-audit", audit_home)
    assert len(items_model.build(audit_home / "self-audit", audit_home)["items"]) == len(first["items"])
    with (audit_home / ".control" / "reports.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "R3", "ts": "2026-07-26T00:00:00+00:00",
                             "routine": "x", "run_id": "x:1", "title": "new", "detail": ""}) + "\n")
    assert len(items_model.build(audit_home / "self-audit", audit_home)["items"]) == len(first["items"]) + 1


def test_missing_sources_yield_an_empty_index(tmp_path):
    memo.reset()
    assert items_model.build(tmp_path / "self-audit", tmp_path)["items"] == []


# ---- filters -----------------------------------------------------------------------------


def test_filter_items_by_type_status_routine_and_search(audit_home):
    items = items_model.build(audit_home / "self-audit", audit_home)["items"]
    assert {i["id"] for i in items_model.filter_items(items, type_="report")} == {"R1", "R2"}
    assert {i["id"] for i in items_model.filter_items(items, status="open")} == {"F2", "D1", "R1"}
    assert {i["id"] for i in items_model.filter_items(items, routine="routine-improver")} == {"R1"}
    assert {i["id"] for i in items_model.filter_items(items, search="clobbered")} == {"R1"}
    # an archive-only item has no prose of its own — its changelog summaries are searchable
    assert "F9" in {i["id"] for i in items_model.filter_items(items, search="revisited")}
    assert {i["id"] for i in items_model.filter_items(items, type_="report", status="open")} \
        == {"R1"}


# ---- the API -----------------------------------------------------------------------------


def test_api_items_merges_filters_and_carries_the_header(api_client):
    c, tmp = api_client
    routines = tmp / "routines"
    audit = routines / "self-audit" / "audit"
    audit.mkdir(parents=True)
    (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(CHANGELOG, encoding="utf-8")
    (routines / ".control").mkdir(parents=True, exist_ok=True)
    (routines / ".control" / "reports.jsonl").write_text(
        "".join(json.dumps(b) + "\n" for b in BUGS), encoding="utf-8")
    memo.reset()

    data = c.get("/api/items").json()
    assert data["exists"] is True
    assert data["report"]["since"]["commit"] == "5854843551"
    assert "findings" not in data["report"]          # the arrays ARE the items now
    assert data["counts"]["type"]["report"] == 2
    assert {r["commit"] for r in data["changelog"]} >= {"dddd4444"}

    reports = c.get("/api/items?type=report").json()
    assert {i["id"] for i in reports["items"]} == {"R1", "R2"}
    assert reports["counts"]["type"]["finding"] == 3    # counts stay UNFILTERED
    assert c.get("/api/items?status=open").json()["total"] == 3
    assert {i["id"] for i in c.get("/api/items?search=clobbered").json()["items"]} == {"R1"}
    assert {i["id"] for i in
            c.get("/api/items?routine=routine-improver").json()["items"]} == {"R1"}


def test_api_items_without_the_self_audit_routine(api_client):
    c, _ = api_client
    assert c.get("/api/items").json() == {
        "exists": False, "routine": "self-audit", "items": [],
        "counts": {"type": {}, "status": {}}, "report": None,
        "last_run": None, "queued": [], "answered_decisions": []}


# ---- priorities (D75): the user's ⚑ reaches the owner's next run -------------------------


def test_priority_store_roundtrip_and_bad_id(tmp_path):
    home = tmp_path / "routines"
    assert priorities.read_priorities(home) == {}
    priorities.set_priority(home, "r1", True)                 # id is normalized upper-case
    assert "R1" in priorities.read_priorities(home)
    priorities.set_priority(home, "R1", False)                # unflag removes the entry
    assert priorities.read_priorities(home) == {}
    with pytest.raises(ValueError):
        priorities.set_priority(home, "X99", True)


def test_priority_flag_floats_the_item_and_busts_the_memo(audit_home):
    first = items_model.build(audit_home / "self-audit", audit_home)["items"]
    assert not any(i.get("priority") for i in first)
    # flag AFTER a memoized build: the store is a fingerprint source, so no reset() needed
    priorities.set_priority(audit_home, "R1", True)
    items = items_model.build(audit_home / "self-audit", audit_home)["items"]
    assert items[0]["id"] == "R1" and items[0]["priority"] is True
    assert not items[1].get("priority")                       # only the flagged one carries it


def test_filter_items_accepts_a_comma_status_list(audit_home):
    items = items_model.build(audit_home / "self-audit", audit_home)["items"]
    active = items_model.filter_items(items, status="open,in_progress")
    assert {i["id"] for i in active} == {"F2", "D1", "R1"}    # the Items page's default view
    assert {i["id"] for i in items_model.filter_items(items, status="open")} \
        == {"F2", "D1", "R1"}                                 # single status unchanged


def test_owner_resolution_and_digest_section(audit_home):
    priorities.set_priority(audit_home, "R1", True)   # untargeted triage row → self-audit
    priorities.set_priority(audit_home, "F1", True)   # findings live in self-audit's report
    with (audit_home / ".control" / "reports.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "R3", "ts": "2026-08-05T10:00:00+02:00",
                             "routine": "self-audit", "run_id": "self-audit:x",
                             "title": "fix the tool", "target": "global-utils-review"}) + "\n")
    priorities.set_priority(audit_home, "R3", True)   # targeted → the target owns it
    sa = priorities.digest_section(audit_home, "self-audit")
    assert "R1" in sa and "F1" in sa and "R3" not in sa
    assert "A finding with no status on disk" in sa           # F/D titles from report.json
    gur = priorities.digest_section(audit_home, "global-utils-review")
    assert "R3" in gur and "fix the tool" in gur and "R1" not in gur
    assert priorities.digest_section(audit_home, "newsletter-digest") == ""


def test_state_digest_carries_the_priority_section(audit_home):
    from rsched.engine.composer import state_digest
    d = audit_home / "self-audit"
    priorities.set_priority(audit_home, "D1", True)
    digest = state_digest(d, [], [], routines_home=audit_home, slug="self-audit")
    assert "PRIORITY items the user flagged" in digest and "D1" in digest
    # a digest without the routines-home context (subrun-shaped call) has no section
    assert "PRIORITY items" not in state_digest(d, [], [])
    # and an unrelated routine's digest stays clean
    other = state_digest(d, [], [], routines_home=audit_home, slug="newsletter-digest")
    assert "PRIORITY items" not in other


def test_api_priority_toggle(api_client):
    c, tmp = api_client
    r = c.post("/api/items/R1/priority", json={"on": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "id": "R1", "on": True}
    assert "R1" in priorities.read_priorities(tmp / "routines")
    assert c.post("/api/items/R1/priority", json={"on": False}).status_code == 200
    assert priorities.read_priorities(tmp / "routines") == {}
    assert c.post("/api/items/notanid/priority", json={"on": True}).status_code == 400
