"""An answer NO run will read is the operator's again — on the page, in the badge (2026-11-09).

Two conversation answers sat unread for seven weeks while this page filed them under Settled as
"answered · queued" — exactly how it shows an answer a run reads tonight. What a person sees
now, and what is asserted here through the real page:

* an answered decision whose routine has no schedule, lane or trigger is in UNDELIVERED, a band
  that waits on a person, with the reason no run will read it;
* the header badge counts it among what waits on him;
* "settle without a run" archives it with no run started, and it stops waiting;
* "deliver now" starts exactly one manual run of the routine that asked.
"""

import yaml
from playwright.sync_api import expect

CAPTION = "you answered, but no run will ever read it"


def _no_clock(ui):
    path = ui.routine_dir("uir") / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["schedule"]["cron"] = ""
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _answered(ui, qid, question):
    ui.seed_run("uir", "20260714-070000", "finished")
    ui.seed_question("uir", qid, question, asked="20260714-070000")
    inbox = ui.routine_dir("uir") / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"answer-{qid}.json").write_text(
        f'{{"qid": "{qid}", "text": "weekly", "source": "web", "ts": "t"}}', encoding="utf-8")


def test_an_undelivered_answer_waits_and_settles_without_a_run(ui, ui_page):
    _no_clock(ui)
    _answered(ui, "q-undel", "How often should the sweep run?")

    ui_page.goto(f"{ui.url}/#/questions")
    head = ui_page.locator(".q-group-head", has_text="Undelivered")
    expect(head).to_contain_text(CAPTION)
    card = ui_page.locator(".question-item", has_text="How often should the sweep run?")
    expect(card).to_contain_text("no run will read this")
    expect(card).to_contain_text("no schedule, lane or trigger")
    expect(ui_page.locator("#q-badge")).to_have_text("1")

    card.locator("button", has_text="settle without a run").click()
    expect(head).to_have_count(0)
    expect(ui_page.locator("#q-badge")).not_to_have_text("1")
    assert ui.runner.fired == []
    record = ui.routine_dir("uir") / "questions" / "answered" / "q-undel.json"
    assert record.is_file()


def test_deliver_now_starts_one_manual_run(ui, ui_page):
    _no_clock(ui)
    _answered(ui, "q-deliv", "Which lane should it join?")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="Which lane should it join?")
    card.locator("button", has_text="deliver now").click()
    expect(ui_page.locator("#toast")).to_contain_text("a run reads it now")
    assert ui.runner.fired == [("uir", "manual")]
