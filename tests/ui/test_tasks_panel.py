"""The routine page's TASKS panel (components/tasks-panel.js, docs/tasks.md): shown only for a
routine whose task layer is on, every task with its state and what the last run did for it, and
the operator's pause / resume between runs landing in the engine-owned store."""

import yaml
from playwright.sync_api import expect

from rsched import tasks

from .conftest import until


def _keep_tasks(ui, slug="uir"):
    d = ui.routine_dir(slug)
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {**(cfg.get("capabilities") or {}), "tasks": "on"}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    tasks.save(d, {"version": 1, "deleted": [], "run": {}, "tasks": [
        {"id": "nanogeofeld", "title": "NanoGeoFeld stewardship", "brief": "b", "state": "active",
         "last": {"run": "uir:1", "at": "2026-10-02T08:11:00+02:00", "outcome": "advanced",
                  "summary": "AP 3.4 revised"}},
        {"id": "ards", "title": "ARDS consulting", "brief": "b", "state": "active",
         "carry": "deferred by uir:1: waits on payment"}]})
    return d


def test_the_tasks_panel_lists_tasks_and_pauses_one(ui, ui_page):
    d = _keep_tasks(ui)
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Tasks')")
    row = ui_page.locator("[data-task='nanogeofeld']")
    expect(row).to_contain_text("NanoGeoFeld stewardship")
    expect(row).to_contain_text("advanced")
    expect(ui_page.locator("[data-task='ards']")).to_contain_text("carried: deferred by uir:1")

    row.locator("[data-task-action='paused']").click()
    until(lambda: tasks.find(tasks.load(d), "nanogeofeld")["state"] == "paused",
          what="the pause reached the store", page=ui_page)
    resume = ui_page.locator("[data-task='nanogeofeld'] [data-task-action='active']")
    expect(resume).to_be_visible()
    resume.click()
    until(lambda: tasks.find(tasks.load(d), "nanogeofeld")["state"] == "active",
          what="the resume reached the store", page=ui_page)


def test_a_routine_without_the_task_layer_shows_no_tasks_section(ui, ui_page):
    ui_page.goto(f"{ui.url}#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Runs')")
    expect(ui_page.locator("h2:has-text('Tasks')")).to_have_count(0)


def test_the_tasks_dial_saves_the_setting(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector(".rgroup-head")
    ui_page.evaluate("() => { for (const d of document.querySelectorAll('details.rgroup')) "
                     "if (d.dataset.group === 'Abilities') d.open = true; }")
    dial = ui_page.locator('#sec-permissions + .panel select[data-setting="tasks"]')
    expect(dial).to_have_value("off")
    dial.select_option("on")
    ui_page.locator(".accept-bar [data-accept]").click()
    until(lambda: (yaml.safe_load((ui.routine_dir("uir") / "routine.yaml").read_text())
                   .get("capabilities") or {}).get("tasks") == "on",
          what="the task layer was saved on", page=ui_page)
