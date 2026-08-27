"""Queued creations on the REAL Decisions page (F328): a scheduled run's proposal shows what
would be created, one click materializes it through the same scaffold path, and discarding tells
the proposing routine so its next run stops waiting.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.paths import atomic_write_json


def _queue(ui, *, pid="pc-20260827-030000-aaaaaa", kind="create_routine", routine="uir",
           fields=None, summary="routine 'fau-comms-steward' from pattern 'general-task'"):
    d = ui.routines / ".control" / "pending-creations"
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / f"{pid}.json", {
        "id": pid, "kind": kind, "routine": routine, "run_id": f"{routine}:20260827-030000",
        "created_at": "2026-08-27T03:00:00+02:00", "summary": summary,
        "fields": fields if fields is not None else {
            "slug": "fau-comms-steward", "name": "FAU comms steward",
            "instruction": "Watch the comms inbox and stage replies for review.",
            "workflow": "general-task"}})
    return pid


def test_band_shows_the_proposal_with_what_would_be_created(ui, ui_page):
    _queue(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    band = ui_page.locator(".q-group-head", has_text="queued creations")
    expect(band).to_be_visible()
    expect(ui_page.locator(".card", has_text="create routine")).to_contain_text(
        "fau-comms-steward")
    expect(ui_page.locator(".card", has_text="create routine")).to_contain_text("proposed by uir")
    # the instruction the routine would be BORN with is what matters before approving
    ui_page.get_by_text("what would be created").click()
    expect(ui_page.locator("pre.doc")).to_contain_text("Watch the comms inbox")


def test_create_it_materializes_and_clears_the_row(ui, ui_page):
    _queue(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.get_by_role("button", name="create it").click()
    expect(ui_page.locator("#toast")).to_contain_text("fau-comms-steward")
    expect(ui_page.locator(".q-group-head", has_text="queued creations")).to_be_hidden()

    made = ui.routines / "fau-comms-steward"
    assert (made / "routine.yaml").is_file() and (made / "main.md").is_file()
    # the proposing routine learns the outcome the ordinary way — a message its next run drains
    msg = next((ui.routines / "uir" / "inbox").glob("msg-pending-*.json"))
    assert "approved and materialized" in json.loads(msg.read_text())["text"]


def test_discard_confirms_then_tells_the_proposer(ui, ui_page):
    _queue(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    ui_page.get_by_role("button", name="discard").click()
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_contain_text("uir is told")
    dlg.get_by_role("button", name="discard").click()

    expect(ui_page.locator(".q-group-head", has_text="queued creations")).to_be_hidden()
    assert not (ui.routines / "fau-comms-steward").exists()
    msg = next((ui.routines / "uir" / "inbox").glob("msg-pending-*.json"))
    assert "discarded" in json.loads(msg.read_text())["text"]


def test_a_group_proposal_reads_as_a_group_change(ui, ui_page):
    _queue(ui, kind="manage_group", summary="create group 'FAU comms'",
           fields={"verb": "create", "name": "FAU comms", "members": ["uir"]})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".card", has_text="group:")
    expect(card).to_contain_text("create")
    expect(card).to_contain_text("FAU comms")


def test_no_proposals_means_no_band_at_all(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".q-group-head", has_text="queued creations")).to_be_hidden()
