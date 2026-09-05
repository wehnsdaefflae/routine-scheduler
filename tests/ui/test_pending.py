"""Queued proposals on the REAL Decisions page (F328): a scheduled run's proposal shows what
would be created, one click materializes it through the same scaffold path, and discarding tells
the proposing routine so its next run stops waiting.

The FINISHED band rides the same queue and is the odd one out: its subject has already changed
state (a routine whose final goal is met has stopped running, derived from its goal document), so
neither button is what stops it. The tests at the foot pin exactly that, because a card implying
"click to stop it" would describe the wrong mechanism.
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


def test_a_library_drift_record_gets_its_own_band_and_no_create_button(ui, ui_page):
    """`daemon/library_watch.py` has filed `library-drift` records since 0.257.0, but the band
    only ever knew the two CREATION kinds: a drift record fell through to the group branch and
    rendered as "group: ?" beside a "create it" button whose only possible answer is a 400.
    Nothing proposed a drift record and nothing can materialize one — the fix is on the routine.
    """
    d = ui.routines / ".control" / "pending-creations"
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / "pc-20260828-040000-bbbbbb.json", {
        "id": "pc-20260828-040000-bbbbbb", "kind": "library-drift", "routine": "uir",
        "run_id": "", "created_at": "2026-08-28T04:00:00+02:00",
        "summary": "uir: secret:ZULIP_API_KEY — needed by zulip. After library change abc12345",
        "fields": {"entity": "uir:secret:ZULIP_API_KEY", "head": "abc12345deadbeef",
                   "node": {"id": "secret:ZULIP_API_KEY", "severity": "blocks",
                            "why": "needed by zulip",
                            "effect": "not in the secrets store — the call runs without it"}}})
    ui_page.goto(f"{ui.url}/#/questions")

    band = ui_page.locator(".q-group-head", has_text="library drift")
    expect(band).to_be_visible(timeout=10_000)
    card = ui_page.locator("[data-drift]")
    expect(card).to_contain_text("uir")
    expect(card).to_contain_text("secret:ZULIP_API_KEY")
    expect(card).to_contain_text("needed by zulip")
    # the fix lives on the routine, so that is where the record points — and nothing here
    # pretends the record can be materialized
    expect(card.get_by_role("link", name="open the routine")).to_have_attribute(
        "href", "#/routine/uir")
    expect(ui_page.get_by_role("button", name="create it")).to_have_count(0)

    card.get_by_role("button", name="dismiss").click()
    expect(ui_page.locator("[data-drift]")).to_have_count(0, timeout=10_000)
    assert list(d.glob("pc-*.json")) == []
    # nothing was messaged: `uir` is the routine the drift BROKE, not a proposer
    assert list((ui.routine_dir("uir") / "inbox").glob("msg-pending-*.json")) == []


# ---- the FINISHED band: a routine reporting its final goal met ------------------------------------

def _queue_goal(ui, *, routine="uir", pid="pc-20260905-090000-bbbbbb"):
    return _queue(ui, pid=pid, kind="goal-reached", routine=routine,
                  summary=f"{routine} reports its final goal met — 1 condition. It has stopped "
                          "running; retire it or reopen the goal.",
                  fields={"conditions": [
                      {"id": "s1", "text": "the application is submitted",
                       "note": "submitted 2026-09-05, receipt filed",
                       "resolved_run": f"{routine}:20260905-080000", "disputed": ""}],
                      "groups": []})


def test_the_finished_band_says_the_routine_has_already_stopped(ui, ui_page):
    _queue_goal(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-goal]")
    expect(card).to_be_visible(timeout=10_000)
    expect(card).to_contain_text("reports its final goal met")
    # the mechanism, stated on the card: neither button is what stopped it
    expect(card).to_contain_text("it has already stopped running")
    expect(card.get_by_role("button", name="retire it")).to_be_visible()
    expect(card.get_by_role("button", name="not yet")).to_be_visible()
    # the EVIDENCE is open by default — this is the thing to read before agreeing a job is over
    expect(card).to_contain_text("the application is submitted")
    expect(card).to_contain_text("the run said: submitted 2026-09-05, receipt filed")


def test_not_yet_reopens_the_goal_and_the_routine_is_scheduled_again(ui, ui_page):
    """Declining has to change the goal DOCUMENT, because retirement is derived from it — dropping
    the record alone would leave the routine unscheduled with nothing left on the page to act on."""
    atomic_write_json(ui.routine_dir("uir") / "state" / "stopping.json", {
        "mode": "all", "groups": [{"id": "g1", "name": "", "mode": "all"}],
        "conditions": [{"id": "s1", "text": "the application is submitted", "status": "met",
                        "group": "g1", "scope": "goal"}]})
    _queue_goal(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator("[data-goal]")).to_be_visible(timeout=10_000)

    ui_page.get_by_role("button", name="not yet").click()
    expect(ui_page.locator("#toast")).to_contain_text("goal reopened")
    expect(ui_page.locator("[data-goal]")).to_have_count(0)

    stored = json.loads(
        (ui.routine_dir("uir") / "state" / "stopping.json").read_text(encoding="utf-8"))
    assert stored["conditions"][0]["status"] == "open"
