"""Routine-page Schedule-once card: an armed one-shot renders (fire time, reason, id),
Cancel removes its spool request, and arming from the UI (datetime-local -> absolute UTC)
writes a new request. DOM and the on-disk request spool are both asserted."""

from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect

from rsched import schedule_once


def _toast(page):
    return page.locator("#toast:not([hidden])")


def _seed(ui, slug="uir", *, reason="seeded check"):
    fire = datetime.now(UTC) + timedelta(days=5)
    return schedule_once.arm(ui.routines, slug, fire_at=fire, reason=reason,
                             requested_by="ui")


def test_schedule_once_card_renders_and_cancels(ui, ui_page):
    _seed(ui)
    ui_page.goto(f"{ui.url}/#/routine/uir")
    row = ui_page.locator(".oneshot-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("one-shot")
    expect(row).to_contain_text("seeded check")
    expect(row).to_contain_text("armed by ui")
    assert len(schedule_once.pending_requests(ui.routines, "uir")) == 1

    row.get_by_role("button", name="cancel").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="cancel one-shot").click()
    # the row vanishing + the spool request going are the durable assertions (a toast expires)
    expect(ui_page.locator(".oneshot-row")).to_have_count(0)
    expect(ui_page.locator(".oneshot-body")).to_contain_text("no one-shot armed")
    assert schedule_once.pending_requests(ui.routines, "uir") == []


def test_schedule_once_arm_from_ui(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(ui_page.locator(".oneshot-body")).to_contain_text("no one-shot armed")
    when_local = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    ui_page.locator("input.oneshot-at").fill(when_local)
    ui_page.locator("input.oneshot-reason").fill("from the ui")
    ui_page.get_by_role("button", name="arm one-shot").click()
    expect(ui_page.locator(".oneshot-row")).to_have_count(1)
    reqs = schedule_once.pending_requests(ui.routines, "uir")
    assert len(reqs) == 1
    rec = schedule_once.read_request(reqs[0])
    assert rec["reason"] == "from the ui" and rec["requested_by"] == "ui"
    assert rec["fire_at"] > datetime.now(UTC).isoformat()
