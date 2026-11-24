"""The Messages page's hook-activity panel on the real console: the one-shots still armed and the
last day's mid-run injections and run-tree hand-backs, each as a ROW the operator can read — the
two questions the hook stream exists to answer for the instance and had no reader for. It sits
where the report ledger is read because that ledger IS the stream's majority, and a test that
loads the Routines page instead would pass while the panel the operator asked for was gone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect

from rsched.hooks import scheduleproj, store

PANEL = ".hookact-panel"


def _arm(ui, hook_id, *, slug="uir", hours=2.0, reason="re-check the seat",
         requested_by="ui") -> None:
    fire = datetime.now(UTC) + timedelta(hours=hours)
    scheduleproj.record_armed(ui.routines, {"id": hook_id, "fire_at": fire.isoformat(),
                                            "reason": reason, "requested_by": requested_by,
                                            "created": datetime.now(UTC).isoformat()}, slug)


def _end(ui, hook_id, event) -> None:
    scheduleproj.record_event(ui.routines, hook_id=hook_id, event=event)


def _inject(ui, *, via, text, run_id="uir:20260715-100000", turn=3) -> None:
    store.record_injection(ui.routines, run_id=run_id, turn=turn, via=via, text=text,
                           routine="uir")


def _runtree(ui, *, delivered, title, addressee="parent", turn=5,
             run_id="uir:20260715-100000") -> None:
    store.record_runtree(ui.routines, hook_id=f"rt-{turn}", routine="uir", run_id=run_id,
                         turn=turn, addressee=addressee, title=title, delivered=delivered)


def test_the_panel_lists_what_fires_next_and_who_spoke_into_which_run(ui, ui_page):
    ui.seed_run("uir", "20260715-100000", "finished", summary="ok")
    _arm(ui, "so-live", hours=2, reason="re-check the seat", requested_by="ui")
    _arm(ui, "so-fired", hours=-6, reason="the one that already went")
    _end(ui, "so-fired", "fired")
    _arm(ui, "so-late", hours=-1, reason="the daemon has not taken this yet")
    _inject(ui, via="web-converse", text="stop and read the gate")
    _runtree(ui, delivered=False, title="the fold is red")

    ui_page.goto(f"{ui.url}/#/messages")

    panel = ui_page.locator(PANEL)
    expect(panel).to_be_visible()

    # FIRES NEXT: the pending fold comes first, so a one-shot that already FIRED is not here —
    # its ending is a later row about the same id, and ordering raw rows would list it as overdue
    fires = panel.locator(".hookact-fire")
    expect(fires).to_have_count(2)
    expect(panel).to_contain_text("re-check the seat")
    expect(panel).to_contain_text("armed by ui")
    expect(panel).not_to_contain_text("the one that already went")
    # a pending one-shot past its instant is shown AS SUCH, never dropped
    expect(panel.locator(".hookact-overdue")).to_have_count(1)
    expect(panel).to_contain_text("the daemon has not taken this yet")

    # SPOKEN INTO RUNS: the channel in words, and a failed run-tree write read as undelivered
    deliveries = panel.locator(".hookact-delivery")
    expect(deliveries).to_have_count(2)
    expect(panel).to_contain_text("you, from the run view's converse box")
    expect(panel).to_contain_text("stop and read the gate")
    expect(panel).to_contain_text("the fold is red")
    expect(panel.locator(".hookact-undelivered")).to_contain_text(
        "not delivered — the write failed")

    # the run a row is about is reachable from the row
    link = panel.locator('a[href="#/run/uir:20260715-100000"]').first
    link.click()
    expect(ui_page).to_have_url(f"{ui.url}/#/run/uir:20260715-100000")


def test_the_routines_page_no_longer_carries_the_panel(ui, ui_page):
    """The move has two halves and only one of them is visible on the page it moved TO."""
    _arm(ui, "so-live", hours=2, reason="re-check the seat", requested_by="ui")
    _inject(ui, via="web-converse", text="stop and read the gate")

    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("table.list, .empty").first).to_be_visible()
    expect(ui_page.locator(PANEL)).to_have_count(0)
    expect(ui_page.locator(".hookact-fire")).to_have_count(0)

    ui_page.goto(f"{ui.url}/#/messages")
    expect(ui_page.locator(PANEL)).to_have_count(1)
    expect(ui_page.locator(PANEL)).to_contain_text("re-check the seat")


def test_a_quiet_instance_renders_both_readings_rather_than_nothing(ui, ui_page):
    """An absent panel and a quiet one are indistinguishable, and only one of them reassures."""
    ui_page.goto(f"{ui.url}/#/messages")

    panel = ui_page.locator(PANEL)
    expect(panel).to_contain_text("nothing armed")
    expect(panel).to_contain_text("nobody spoke")
    expect(panel.locator(".hookact-fire")).to_have_count(0)
