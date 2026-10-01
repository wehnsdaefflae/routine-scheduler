"""Queued proposals on the REAL Decisions page (F328): a scheduled run's proposal shows what
would be created, one click materializes it through the same scaffold path, and discarding tells
the proposing routine so its next run stops waiting.

The FINISHED band rides the same queue and is the odd one out: its subject has already changed
state (a routine whose finish line is reached has stopped running, derived from its finish-line
document), so neither button is what stops it. The tests at the foot pin exactly that, because a
card implying "click to stop it" would describe the wrong mechanism.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import expect

from rsched.paths import atomic_write_json


def _queue(ui, *, pid="pc-20260827-030000-aaaaaa", kind="create_routine", routine="uir",
           fields=None, summary="routine 'fau-comms-steward' from pattern 'general-task'",
           run_id=None):
    d = ui.routines / ".control" / "pending-creations"
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / f"{pid}.json", {
        "id": pid, "kind": kind, "routine": routine,
        "run_id": f"{routine}:20260827-030000" if run_id is None else run_id,
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


def test_the_header_badge_counts_a_standing_proposal_until_it_is_decided(ui, ui_page):
    """A proposal waits on a person exactly as a question does, but the badge read questions
    only — a met goal or a queued creation sat on the Decisions page with the badge at 0.
    Deciding it takes it off the badge at once, without waiting for a bus event."""
    _queue(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator("#q-badge")).to_have_text("1")
    ui_page.get_by_role("button", name="discard").click()
    ui_page.locator(".modal-overlay").get_by_role("button", name="discard").click()
    expect(ui_page.locator("#q-badge")).to_be_hidden()


def test_a_lane_proposal_reads_as_a_lane_change(ui, ui_page):
    _queue(ui, kind="manage_lane", summary="create lane 'FAU comms'",
           fields={"verb": "create", "name": "FAU comms", "members": ["uir"]})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".card", has_text="lane:")
    expect(card).to_contain_text("create")
    expect(card).to_contain_text("FAU comms")


def test_no_proposals_means_no_band_at_all(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator(".q-group-head", has_text="queued creations")).to_be_hidden()


def test_a_library_drift_record_gets_its_own_band_and_no_create_button(ui, ui_page):
    """`daemon/library_watch.py` has filed `library-drift` records since 0.257.0, but the band
    only ever knew the two CREATION kinds: a drift record fell through to the branch that
    renders a lane change and came out as "lane: ?" beside a "create it" button whose only
    possible answer is a 400. Nothing proposed a drift record and nothing can materialize one —
    the fix is on the routine.
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
    expect(band).to_be_visible()
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
    expect(ui_page.locator("[data-drift]")).to_have_count(0)
    assert list(d.glob("pc-*.json")) == []
    # nothing was messaged: `uir` is the routine the drift BROKE, not a proposer
    assert list((ui.routine_dir("uir") / "inbox").glob("msg-pending-*.json")) == []


def test_two_gaps_from_one_commit_are_one_card(ui, ui_page):
    """The watcher files one record per GAP, so a library commit that cost a routine two
    permissions produced two cards with the same routine, the same commit and the same "what
    broke" — and the reader had to work out they were one event. One commit is one card."""
    d = ui.routines / ".control" / "pending-creations"
    d.mkdir(parents=True, exist_ok=True)
    for n, ent in enumerate(["secret:ZULIP_API_KEY", "util:zulip"]):
        atomic_write_json(d / f"pc-20260828-04000{n}-cccccc.json", {
            "id": f"pc-20260828-04000{n}-cccccc", "kind": "library-drift", "routine": "uir",
            "run_id": "", "created_at": "2026-08-28T04:00:00+02:00",
            "summary": f"uir: {ent}", "fields": {
                "entity": f"uir:{ent}", "head": "faf62a6d99",
                "node": {"id": ent, "severity": "blocks", "why": "held, but not switched on",
                         "effect": "it fails closed"}}})
    ui_page.goto(f"{ui.url}/#/questions")

    card = ui_page.locator("[data-drift]")
    expect(card).to_have_count(1)
    expect(card).to_have_attribute("data-drift-count", "2")
    expect(card).to_contain_text("lost 2 permissions")
    expect(card).to_contain_text("faf62a6d")
    # both gaps are named on the one card — grouping folds the duplication, not the content
    expect(card).to_contain_text("secret:ZULIP_API_KEY")
    expect(card).to_contain_text("util:zulip")

    # and one dismissal settles the whole event, which is what the reader means by it
    card.get_by_role("button", name="dismiss all 2").click()
    expect(ui_page.locator("[data-drift]")).to_have_count(0)
    assert list(d.glob("pc-*.json")) == []


# ---- the FINISHED band: a routine whose finish line is reached --------------------------------

_PROVED = {"id": "g1", "text": "the application is submitted", "judge": "run", "date": "",
           "status": "met", "evidence": "submitted 2026-09-05, receipt filed",
           "met_run": "uir:20260905-080000", "disputed": ""}


def _queue_goal(ui, *, routine="uir", pid="pc-20260905-090000-bbbbbb", outcomes=None, until="",
                why="every outcome of its finish line is reached", run_id=None):
    """The card engine/goalreached.propose files: the finish line's outcomes, its `until` and
    why it counts as reached."""
    return _queue(ui, pid=pid, kind="goal-reached", routine=routine, run_id=run_id,
                  summary=f"{routine} reached its finish line — {why}. It has stopped running; "
                          "retire it, or reopen the finish line to keep it going.",
                  fields={"outcomes": [_PROVED] if outcomes is None else outcomes,
                          "until": until, "why": why})


def test_the_finished_band_says_the_routine_has_already_stopped(ui, ui_page):
    _queue_goal(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-goal]")
    expect(card).to_be_visible()
    expect(card).to_contain_text("reached its finish line")
    expect(card).to_contain_text("every outcome of its finish line is reached")
    # the mechanism, stated on the card: neither button is what stopped it
    expect(card).to_contain_text("it has already stopped running")
    expect(card.get_by_role("button", name="retire it")).to_be_visible()
    expect(card.get_by_role("button", name="not yet")).to_be_visible()
    # the EVIDENCE is open by default — this is the thing to read before agreeing a job is over
    outcome = card.locator('[data-goal-outcome="g1"]')
    expect(outcome).to_contain_text("the application is submitted")
    expect(outcome).to_contain_text("a run proved it")
    expect(outcome).to_contain_text("the run said: submitted 2026-09-05, receipt filed")
    expect(outcome.locator('a[href="#/run/uir:20260905-080000"]')).to_be_visible()


def test_not_yet_reopens_the_finish_line_and_the_routine_is_scheduled_again(ui, ui_page):
    """Declining has to change the finish-line DOCUMENT, because retirement is derived from it —
    dropping the record alone would leave the routine unscheduled with nothing left on the page
    to act on."""
    line = ui.routine_dir("uir") / "state" / "finish-line.json"
    atomic_write_json(line, {"outcomes": [_PROVED], "until": ""})
    _queue_goal(ui)
    ui_page.goto(f"{ui.url}/#/questions")
    expect(ui_page.locator("[data-goal]")).to_be_visible()

    ui_page.get_by_role("button", name="not yet").click()
    expect(ui_page.locator("#toast")).to_contain_text("finish line reopened (g1)")
    expect(ui_page.locator("[data-goal]")).to_have_count(0)

    stored = json.loads(line.read_text(encoding="utf-8"))
    assert stored["outcomes"][0]["status"] == "open"
    assert stored["outcomes"][0]["evidence"] == "submitted 2026-09-05, receipt filed"   # kept


def test_a_line_the_calendar_reached_is_changed_by_its_date_not_reopened(ui, ui_page):
    """Reopening changes nothing a date decides, so the card offers no "not yet": it sends the
    reader to the routine's Goal settings, where the date is moved, with the folds opened."""
    _queue_goal(ui, outcomes=[], until="2026-01-31", why="its end date 2026-01-31 has passed",
                run_id="")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator("[data-goal]")
    expect(card).to_contain_text("its end date 2026-01-31 has passed")
    expect(card).to_contain_text("no run involved")
    expect(card).to_contain_text("stop scheduling after 2026-01-31")
    expect(card.get_by_role("button", name="not yet")).to_have_count(0)
    card.locator("[data-goal-date]").click()
    ui_page.wait_for_url(re.compile(r"#/routine/uir\?section=goal$"))
    expect(ui_page.locator("#sec-goal")).to_be_in_viewport()
    expect(ui_page.locator("[data-finish-line]")).to_be_visible()


def test_a_disputed_outcome_reads_in_the_warning_colour(ui, ui_page):
    """A transcript check's standing objection is the most important line on the card — and it
    wore an `err-text` class no stylesheet defined, so it read as plain body ink. It uses the
    finish-line card's own `.fl-disputed`."""
    _queue_goal(ui, outcomes=[{**_PROVED, "disputed": "no receipt in the transcript"}])
    ui_page.goto(f"{ui.url}/#/questions")
    note = ui_page.locator('[data-goal-outcome="g1"] .fl-disputed')
    expect(note).to_contain_text("no receipt in the transcript")
    probe = ui_page.evaluate("""() => { const p = document.createElement('span');
      p.style.color = 'var(--warn)'; document.body.append(p);
      const c = getComputedStyle(p).color; p.remove(); return c; }""")
    assert note.evaluate("e => getComputedStyle(e).color") == probe
