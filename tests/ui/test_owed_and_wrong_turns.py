"""2026-11-19 on the real console: the blocked-work list holds only what still holds, wrong turns
have their own by-cause panel, and an obligation waiting on the operator is his Decisions card.

The three were one complaint: the blocked-work list had 285 incidents, ~230 of them wrong turns
that were re-driven while their runs went on, beside standing rows for conditions already over
and obligations no routine could ever clear by itself.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.engine import obligations
from rsched.paths import atomic_write_json, read_json

from .conftest import until


def _stream(ui, rows) -> None:
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    with (control / "health-events.jsonl").open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _now(page) -> str:
    return page.evaluate("() => new Date().toISOString().replace('Z', '+00:00')")


def _section(page, name):
    return page.locator(".stat-section", has=page.get_by_role("heading", name=name))


def test_wrong_turns_leave_blocked_work_for_their_own_panel_by_cause(ui, ui_page):
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")
    ts = _now(ui_page)
    _stream(ui, [
        {"ts": ts, "event": "wrong_turn_detected", "routine": "uir", "run_id": "uir:1",
         "reason": "schema_rejected", "model": "m1",
         "detail": "turn 35: start_line: -40 is less than the minimum of 1"},
        {"ts": ts, "event": "wrong_turn_detected", "routine": "other", "run_id": "other:1",
         "reason": "schema_rejected", "model": "m2",
         "detail": "turn 23: start_line: -5 is less than the minimum of 1"},
    ])
    ui_page.goto(f"{ui.url}/#/stats")
    expect(_section(ui_page, "Blocked work")).to_contain_text("nothing blocked")
    panel = _section(ui_page, "Wrong turns")
    expect(panel).to_contain_text("2 wrong turns from 1 causes")
    expect(panel).to_contain_text("start_line: N is less than the minimum of N")
    expect(panel.get_by_role("link", name="uir")).to_be_visible()


def test_a_standing_condition_that_ended_leaves_the_table(ui, ui_page):
    """An obligation row whose routine no longer holds anything overdue is resolved: named in
    one line, not a row of the table."""
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")
    _stream(ui, [{"ts": _now(ui_page), "event": "obligation_overdue", "routine": "uir",
                  "run_id": "", "detail": "b1 open 9 days: x"}])
    ui_page.goto(f"{ui.url}/#/stats")
    section = _section(ui_page, "Blocked work")
    expect(section).to_contain_text("everything that was blocked this week has been resolved")
    expect(section).to_contain_text("1 resolved since — obligation_overdue (uir)")
    expect(section.locator("table")).to_have_count(0)


def test_an_obligation_waiting_on_the_operator_is_his_card(ui, ui_page):
    routine = ui.routines / "uir"
    atomic_write_json(routine / obligations.FILE, {"open": [
        {"id": "b17", "text": "video 1 is approved", "quote": "approve it", "since": "2026-10-26",
         "from_run": "uir:1", "remains": "the new cut needs your listen", "waits": "operator",
         "waits_since": "2026-11-10"}]})
    d = ui.routines / ".control" / "pending-creations"
    d.mkdir(parents=True, exist_ok=True)
    pid = "pc-20261119-090000-cccccc"
    atomic_write_json(d / f"{pid}.json", {
        "id": pid, "kind": "owed-obligation", "routine": "uir", "run_id": "",
        "created_at": "2026-11-19T09:00:00+01:00", "summary": "uir waits on you for b17",
        "fields": {"routine": "uir", "id": "b17", "from_run": "uir:1",
                   "text": "video 1 is approved", "quote": "approve it",
                   "remains": "the new cut needs your listen", "since": "2026-10-26",
                   "waits_since": "2026-11-10", "days": 24}})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-owed='uir/b17']")
    expect(card).to_contain_text("the new cut needs your listen")
    card.get_by_role("button", name="it's done").click()
    expect(ui_page.locator("#toast")).to_contain_text("b17 marked done")
    until(lambda: not read_json(routine / obligations.FILE)["open"])
    assert read_json(routine / obligations.FILE)["settled"][0]["how"] == "met"
