"""The routine page's MODEL TRIAL chip (components/trialchip.js over rsched/trials.py).

A routine on a trial runs on another model than its own for a while — production-relevant, so
the header says it in ONE chip beside the run chip, counts the runs from the durable stream as
they land, and points a finished trial at its results. No trial, no chip; a trial the catalog
cannot serve is a PROBLEM, said in the page's problem panel rather than as a trial.
"""

import yaml
from playwright.sync_api import expect

from rsched.health_events import log_workflow_usage

TRIAL = {"id": "t-20261008-m", "models": {"main": "m"}, "runs": 5,
         "reason": "is the routine's own model overkill for a digest?"}
CHIP = ".page-head [data-trial]"


def _trial(ui, trial):
    path = ui.routine_dir("uir") / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["trial"] = trial
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _record(ui, n: int, trial: str = TRIAL["id"]):
    log_workflow_usage(ui.routines, routine="uir", run_id=f"uir:2026100{n}-120000",
                       workflow="w", depth=0, status="ok", turns=3, tokens=10,
                       fingerprint={"engine": "x", "model": "m", "trial": trial})


def test_an_active_trial_is_one_chip_that_counts_its_runs(ui, ui_page):
    _trial(ui, TRIAL)
    _record(ui, 1)
    _record(ui, 2)
    ui_page.goto(f"{ui.url}#/routine/uir")
    chip = ui_page.locator(CHIP)
    expect(chip).to_have_count(1)
    expect(chip).to_have_text("trial · m · 2 of 5 runs")
    assert TRIAL["reason"] in (chip.get_attribute("title") or "")
    # beside the run chip, not instead of it
    expect(ui_page.locator(".page-head .chip.idle")).to_be_visible()

    # a run finishing repaints the head — and with it the trial's count
    _record(ui, 3)
    ui_page.evaluate("() => window.dispatchEvent(new CustomEvent('rsched-bus', {detail: "
                     "{event: 'run_finished', run_id: 'uir:20261003-120000'}}))")
    expect(ui_page.locator(CHIP)).to_have_text("trial · m · 3 of 5 runs")


def test_a_finished_trial_points_at_its_results(ui, ui_page):
    _trial(ui, {**TRIAL, "runs": 1})
    _record(ui, 1)
    ui_page.goto(f"{ui.url}#/routine/uir")
    link = ui_page.locator(f"{CHIP}[data-trial='finished']")
    expect(link).to_have_text("trial finished · results in Development")
    expect(link).to_have_attribute("href", "#/changes/uir")


def test_no_trial_no_chip_and_an_unservable_one_is_a_problem(ui, ui_page):
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector(".page-head h1")
    expect(ui_page.locator(".page-head .chip.idle")).to_be_visible()
    expect(ui_page.locator(CHIP)).to_have_count(0)

    _trial(ui, {**TRIAL, "models": {"main": "retired-model"}})
    ui_page.reload()
    ui_page.wait_for_selector(".page-head h1")
    expect(ui_page.locator(".panel.err", has_text="'retired-model' is not in the model catalog")
           ).to_be_visible()
    expect(ui_page.locator(CHIP)).to_have_count(0)
