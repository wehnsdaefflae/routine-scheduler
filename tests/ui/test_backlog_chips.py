"""The Messages page's headline number says whom it is waiting on (components/backlog-chips.js).

On 2026-10-18 the active chip read 100 and one of those items was the operator's to act on;
the rest could only fall when another routine acted. The chips split the worklist by owner,
name the operator's share as "you", count reports still in transit apart, and an owner chip
lists exactly that owner's live items — of every type, since the page opens on summaries.
"""

import json

from playwright.sync_api import expect

REPORT = {
    "schema": 1, "run_id": "self-audit:20261018-010000",
    "generated": "2026-10-18T01:00:00+00:00",
    "since": {"commit": "abc1234f", "window": "1 run"},
    "summary": "two handed, one for you",
    "findings": [
        {"id": "F1", "severity": "problem", "status": "in_progress", "title": "Handed one",
         "detail": "", "owner": "scheduler-builder"},
        {"id": "F2", "severity": "problem", "status": "in_progress", "title": "Handed two",
         "detail": "", "owner": "scheduler-builder"}],
    "decisions": [{"id": "D1", "status": "open", "title": "Yours to pick", "detail": "",
                   "options": ["do it", "leave as-is"], "owner": "operator"}],
}

IN_TRANSIT = {"id": "R1", "ts": "2026-10-17T08:00:00+00:00", "routine": "self-audit",
              "run_id": "self-audit:20261017-080000", "title": "For the radar", "detail": "",
              "target": "radar"}

#: Addressed to a routine this instance does not have: no inbox, no run, nobody who can drain
#: it (F738). It must be counted apart and SAID, never left on the worklist as live work.
UNROUTABLE = {"id": "R2", "ts": "2026-10-16T07:06:46+00:00", "routine": "steward-hub-maintainer",
              "run_id": "steward-hub-maintainer:20261016-045256", "detail": "",
              "title": "Nobody can drain this one", "target": "deleted-radar"}


def test_the_active_chip_says_whom_the_worklist_waits_on(ui, ui_page, make_routine):
    make_routine(slug="self-audit")
    make_routine(slug="radar")                       # a routine that runs: R1 is in transit
    audit = ui.routines / "self-audit" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (IN_TRANSIT, UNROUTABLE)), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/messages")             # the default view: summaries
    bar = ui_page.locator(".filterbar")
    # R2 is addressed to nothing, so it is NOT in the 3 the worklist waits on
    expect(bar.locator(".tag", has_text="active")).to_contain_text("active 3")
    expect(bar.locator(".tag", has_text="in transit")).to_have_text("in transit 1")
    expect(bar.locator(".tag", has_text="unroutable")).to_have_text("unroutable 1")
    expect(bar.locator(".tag", has_text="scheduler-builder")).to_have_text("scheduler-builder 2")

    bar.locator(".tag", has_text="you 1").click()
    expect(ui_page.locator("#ref-D1")).to_be_visible()
    expect(ui_page.locator("#ref-F1")).to_have_count(0)
    expect(ui_page.locator("#ref-R1")).to_have_count(0)
    assert "owner=operator" in ui_page.url

    bar.locator(".tag", has_text="you 1").click()    # off again: back to every owner
    expect(ui_page.locator("#ref-F1")).to_be_visible()
