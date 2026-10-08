"""The routine page's GOAL: the finish line (a setting) and what a finished run delivers (the
recipe's `## Done when`, read-only).

A routine either runs until it is switched off, or it has a finish line — outcomes, each naming
who decides it is reached (the calendar, the run, or the person), plus an optional date after
which scheduling stops either way. The finish line is edited in the settings draft and saved by
the page's one accept. What one finished run delivers belongs to the recipe; the page shows it
beside the verdict each of the last runs gave it, each verdict a link to its run.

And a run's own page shows THAT run's accounting: what it reported, at its end, for each line.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .conftest import until
from .helpers import accept

CHANGED = re.compile(r"\bsf-changed\b")

DONE_WHEN = """

## Done when

- d1 · gather — every due source was read, or recorded as unreadable
- d2 — the ledger records what was decided and why
"""


def _finish_line(ui, slug="uir") -> dict:
    path = ui.routines / slug / "state" / "finish-line.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _section(page, anchor):
    panel = page.locator(f"#{anchor} + .panel")
    expect(panel).to_be_visible()
    return panel


def test_no_finish_line_says_what_one_would_do(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _section(ui_page, "sec-goal")
    expect(panel.locator(".fl-empty")).to_contain_text(
        "This routine runs until you switch it off.")
    expect(panel.locator(".fl-empty")).to_contain_text(
        "a Decisions card asks you to confirm retiring it")


def test_an_outcome_the_run_proves_is_a_setting_like_any_other(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _section(ui_page, "sec-goal")
    panel.locator('[data-add-outcome="run"]').click()
    row = panel.locator(".fl-row").first
    row.locator(".fl-text").fill("the grant application is submitted")
    expect(row.locator(".fl-judge")).to_have_value("run")
    # every outcome says what reaching it does, before anything is saved
    expect(row.locator(".fl-then")).to_contain_text("Reached when: a run proves it")
    expect(row.locator(".fl-then")).to_contain_text("Then: scheduling stops and you get a card.")
    expect(row.locator(".fl-then")).to_contain_text(
        "Until then: every run reports the remaining distance here.")
    expect(ui_page.locator('.sf-field[data-field="finish_line"]')).to_have_class(CHANGED)
    assert not _finish_line(ui)                                  # a draft until accepted

    accept(ui_page)
    until(lambda: _finish_line(ui).get("outcomes"), what="the accepted finish line")
    [outcome] = _finish_line(ui)["outcomes"]
    assert outcome["text"] == "the grant application is submitted"
    assert outcome["judge"] == "run" and outcome["status"] == "open"
    assert outcome["id"] == "g1"                                 # the server numbers it


def test_only_you_judge_an_outcome_of_yours_and_the_date_stops_it_either_way(ui, ui_page):
    atomic_write_json(ui.routines / "uir" / "state" / "finish-line.json", {
        "outcomes": [{"id": "g1", "text": "I am happy with the landing page", "judge": "you",
                      "status": "open"}], "until": ""})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _section(ui_page, "sec-goal")
    row = panel.locator('.fl-row[data-outcome="g1"]')
    expect(row.locator(".fl-then")).to_contain_text("Reached when: you judge it reached")
    row.locator("[data-judge-reached]").click()
    expect(row.locator(".fl-reached")).to_have_text("you judged it reached")

    until_input = panel.locator("[data-until]")
    until_input.fill("2031-01-31")
    expect(panel.locator(".fl-until .fl-then")).to_contain_text(
        "scheduling stops whatever the outcomes say and you get a card")
    accept(ui_page)
    until(lambda: _finish_line(ui).get("until") == "2031-01-31", what="the end date")
    assert _finish_line(ui)["outcomes"][0]["status"] == "met"


def test_a_date_outcome_needs_its_date(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _section(ui_page, "sec-goal")
    panel.locator('[data-add-outcome="date"]').click()
    row = panel.locator(".fl-row").first
    row.locator(".fl-text").fill("the submission deadline")
    expect(row.locator(".fl-problem")).to_have_text("a date outcome needs its date")
    row.locator(".fl-date").fill("2031-03-01")
    expect(row.locator(".fl-problem")).to_have_count(0)
    expect(row.locator(".fl-then")).to_contain_text("2031-03-01 arrives")
    expect(row.locator(".fl-then")).to_contain_text("the calendar decides")
    accept(ui_page)
    until(lambda: _finish_line(ui).get("outcomes"), what="the date outcome")
    assert _finish_line(ui)["outcomes"][0]["date"] == "2031-03-01"


def test_the_latest_distance_links_to_the_run_that_reported_it(ui, ui_page):
    ui.seed_run("uir", "20260714-070000", "finished", summary="done")
    atomic_write_json(ui.routines / "uir" / "state" / "finish-line.json", {
        "outcomes": [{"id": "g1", "text": "the grant application is submitted",
                      "judge": "run", "status": "open",
                      "distance": "two sections still missing their figures",
                      "distance_run": "uir:20260714-070000",
                      "distance_ts": "2026-07-14T07:10:00+00:00"}], "until": ""})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    report = _section(ui_page, "sec-goal").locator('[data-distance="g1"]')
    expect(report).to_contain_text("two sections still missing their figures")
    expect(report.locator("a")).to_have_attribute("href", "#/run/uir:20260714-070000")


def test_what_a_finished_run_delivers_is_read_only_with_the_last_verdicts(ui, ui_page):
    main = ui.routines / "uir" / "main.md"
    main.write_text(main.read_text(encoding="utf-8") + DONE_WHEN, encoding="utf-8")
    for ts, d1 in (("20260712-070000", "met: all read"), ("20260713-070000",
                                                          "unmet: the mailbox refused the login")):
        run = ui.seed_run("uir", ts, "finished", summary="done")
        status = json.loads((run / "status.json").read_text(encoding="utf-8"))
        status["accounting"] = [f"d1 {d1}", "d2 not due: nothing was decided"]
        atomic_write_json(run / "status.json", status)

    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _section(ui_page, "sec-done-when")
    d1 = panel.locator('.dw-row[data-done="d1"]')
    expect(d1).to_contain_text("every due source was read, or recorded as unreadable")
    expect(d1.locator(".dw-stage")).to_have_text("produced by stage gather")
    cells = d1.locator(".dw-cell")
    expect(cells).to_have_count(2)
    # oldest → newest: each mark opens its run; an unmet mark carries what remained
    expect(cells.nth(0)).to_have_class(re.compile(r"\bmet\b"))
    expect(cells.nth(1)).to_have_class(re.compile(r"\bunmet\b"))
    expect(cells.nth(1)).to_have_attribute("href", "#/run/uir:20260713-070000")
    expect(cells.nth(1)).to_have_attribute("title", re.compile("the mailbox refused the login"))
    expect(panel.locator('.dw-row[data-done="d2"] .dw-cell').nth(0)).to_have_class(
        re.compile(r"\bnot-due\b"))
    # read-only: the recipe owns it; the way to change it is named
    expect(panel.locator("input, textarea, select")).to_have_count(0)
    expect(panel.locator("[data-revise-recipe]")).to_have_attribute(
        "href", "#/run/uir:20260713-070000?revise=1")
    expect(panel).to_contain_text("Nothing here limits how much a run does.")
    expect(panel).to_contain_text("Things a run must never do live in Permissions, General "
                                  "rules and Reminders.")


def test_a_runs_page_shows_that_runs_own_accounting(ui, ui_page):
    """The run rail's goal section is THIS run's accounting, labelled with the lines it
    answered for. (Needs the run detail to carry the status's `accounting` list.)"""
    main = ui.routines / "uir" / "main.md"
    main.write_text(main.read_text(encoding="utf-8") + DONE_WHEN, encoding="utf-8")
    run = ui.seed_run("uir", "20260713-070000", "finished", summary="done")
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status["accounting"] = ["d1 unmet: the mailbox refused the login",
                            "d2 met: the ledger has the decision"]
    atomic_write_json(run / "status.json", status)

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:20260713-070000")
    acct = ui_page.locator("[data-run-accounting]")
    d1 = acct.locator('[data-acct="d1"]')
    expect(d1).to_contain_text("every due source was read, or recorded as unreadable")
    expect(d1).to_contain_text("unmet")
    expect(d1.locator(".acct-note")).to_have_text("the mailbox refused the login")
    expect(acct.locator('[data-acct="d2"]')).to_have_class(re.compile(r"\bv-met\b"))
    caps = ui_page.locator(".run-rail .rail-cap")
    assert [c.strip().lower() for c in caps.all_inner_texts()] == [
        "artifacts", "files", "state", "tasks", "goal"]


def test_revise_recipe_lands_on_the_runs_message_box_ready_to_edit_the_recipe(ui, ui_page):
    ui.seed_run("uir", "20260713-070000", "finished", summary="done")
    ui_page.goto(f"{ui.url}/#/run/uir:20260713-070000?revise=1")
    box = ui_page.locator(".composer textarea")
    expect(box).to_be_focused()
    expect(box).to_have_attribute("placeholder", re.compile("may edit the routine's recipe"))


def test_run_now_takes_an_optional_brief(ui, ui_page):
    """The routine page's Run now offers one optional line: the run started by hand answers for
    it instead of its recipe's Done when. Left empty, it is an ordinary run."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    brief = ui_page.locator("[data-run-brief]")
    expect(brief).to_be_visible()
    brief.fill("check only the new grant call")
    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/routines/uir/run")) as sent:
        ui_page.locator("[data-run-now]").click()
    assert sent.value.post_data_json == {"brief": "check only the new grant call"}
    until(lambda: ui.runner.fired == [("uir", "manual")], what="the manual fire")


def test_a_run_started_with_a_brief_says_so_and_accounts_for_it(ui, ui_page):
    """The brief is the run's goal b1 and a mid-run request its b2 (engine/goals.py): the rail
    labels every goal line from the run's own status, under one caption."""
    from rsched.engine import goals

    run = ui.seed_run("uir", "20260714-070000", "finished", summary="done")
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status["brief"] = "check only the new grant call"
    status["goals"] = [*goals.seed("check only the new grant call"),
                       goals.blank("b2", "the funder is told the new date", "tell the funder",
                                   source="person", heard=1, turn=4)]
    status["accounting"] = ["b1 met: the new call is read and filed",
                            "b2 unmet: their mailbox bounced"]
    atomic_write_json(run / "status.json", status)
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    expect(ui_page.locator("[data-run-brief]")).to_contain_text("check only the new grant call")
    b1 = ui_page.locator('[data-run-accounting] [data-acct="b1"]')
    expect(b1).to_contain_text("check only the new grant call")
    expect(b1.locator(".acct-note")).to_have_text("the new call is read and filed")
    b2 = ui_page.locator('[data-run-accounting] [data-acct="b2"]')
    expect(b2).to_contain_text("the funder is told the new date")
    expect(b2).to_have_class(re.compile(r"\bv-unmet\b"))
    expect(ui_page.locator("[data-run-accounting] .acct-cap")).to_have_text(
        ["what was asked of this run"])


@pytest.mark.browser_context_args(timezone_id="Pacific/Kiritimati")
def test_a_date_outcome_is_reached_on_the_local_day(ui, ui_page):
    """The scheduler judges a date by its LOCAL day (engine/finishline.today); the editor used
    the UTC day. At 12:00 UTC it is already 02:00 on the next day at UTC+14, so a date outcome
    for that day has come — the editor said it had not."""
    ui_page.clock.set_fixed_time("2026-07-15T12:00:00Z")
    atomic_write_json(ui.routines / "uir" / "state" / "finish-line.json", {
        "outcomes": [{"id": "g1", "text": "the submission deadline", "judge": "date",
                      "date": "2026-07-16", "status": "open"}], "until": ""})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _section(ui_page, "sec-goal")
    expect(panel.locator('.fl-row[data-outcome="g1"] .fl-reached')).to_have_text("reached")
