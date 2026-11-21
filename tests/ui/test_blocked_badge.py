"""The rail's count of BLOCKS ONLY THE OPERATOR CAN CLEAR (F796), through the real page."""

import json
import re

from playwright.sync_api import expect


def _blocked(ui, rows) -> None:
    """Put health events on the instance's stream, the way the engine's writers do."""
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    with (control / "health-events.jsonl").open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _now_iso(page) -> str:
    """A stamp inside the fold's window, in the shape `ids.now_iso` writes (local time with its
    offset) — the fold compares instants, so the offset must be there."""
    return page.evaluate("() => new Date().toISOString().replace('Z', '+00:00')")


def test_a_stale_backup_lights_the_rails_blocked_badge(ui, ui_page):
    """The one thing this item is for: a block nothing inside the system will clear reaches a
    count in the rail, on every page, instead of one panel's 7-day window.
    """
    _blocked(ui, [{"ts": _now_iso(ui_page), "event": "backup_stale", "routine": "",
                   "run_id": "", "detail": "no completed snapshot for 4 days"}])
    ui_page.goto(f"{ui.url}/#/routines")

    badge = ui_page.locator("#blocked-badge")
    expect(badge).to_have_count(1)
    expect(badge).to_have_text("1")


def test_the_badge_says_what_only_he_can_do_about_it(ui_page, ui):
    """The sentence is the SERVER's (`readmodels/operator_blocks.OPERATOR_CLEARABLE`), so the
    console renders a judgement it was not compiled with — the same contract `vocabulary` has.
    """
    _blocked(ui, [{"ts": _now_iso(ui_page), "event": "backup_stale", "routine": "",
                   "run_id": "", "detail": "no completed snapshot for 4 days"}])
    ui_page.goto(f"{ui.url}/#/routines")

    badge = ui_page.locator("#blocked-badge")
    expect(badge).to_have_count(1)
    expect(badge).to_have_attribute("title", re.compile("journalctl"))


def test_a_block_a_run_will_clear_leaves_the_badge_dark(ui, ui_page):
    """A commit that did not land is taken by a later commit — nothing about it waits for a
    person. A badge counting the blocks the system clears by itself would never be dark.
    """
    _blocked(ui, [{"ts": _now_iso(ui_page), "event": "commit_failed",
                   "routine": "uir", "run_id": "uir:20260715-100000",
                   "detail": "a commit into a versioned repo did not land"}])
    ui_page.goto(f"{ui.url}/#/routines")

    badge = ui_page.locator("#blocked-badge")
    expect(badge).to_have_count(1)
    expect(badge).to_be_hidden()


def test_nothing_blocked_hides_the_badge(ui, ui_page):
    """Zero is the good reading: the element exists (so the count can appear without a reload)
    and carries nothing.
    """
    ui_page.goto(f"{ui.url}/#/routines")

    badge = ui_page.locator("#blocked-badge")
    expect(badge).to_have_count(1)
    expect(badge).to_be_hidden()


def test_the_decisions_badge_still_counts_after_the_move(ui, ui_page):
    """`paintBadge` moved out of app.js into header-badges.js to pay for this feature by
    SHRINKING the entry module. The decisions count is the thing that move could break, so it
    is pinned here beside the badge that replaced it.
    """
    pending = ui.routines / "uir" / "questions" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "q-20260715-100000-1.json").write_text(json.dumps({
        "id": "q-20260715-100000-1", "routine": "uir", "run_id": "uir:20260715-100000",
        "question": "which folder?", "options": [], "asked_at": "2026-07-15T10:00:00+02:00",
        "mode": "deferred"}), encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/routines")

    badge = ui_page.locator("#q-badge")
    expect(badge).to_have_count(1)
    expect(badge).to_have_text("1")
