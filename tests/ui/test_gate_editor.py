"""The run gate in the console: a list of CHECKS, edited in the settings draft, tested in place.

A gate decides WHETHER a scheduled fire becomes a run at all, so it sits in the Schedule & gate
group beside the cadence. It is a list of checks, each a known question with parameters, rendered
from the vocabulary the server publishes (GET /api/gate/kinds) — so a kind cannot exist on one
side only. The routine's own `scripts/admit.py` is one more check, edited in place below its
row. "Test the gate now" asks the checks as shown — saved or not — through the real admission
path and starts no run.

The file on disk is the proof of a save: `routine.yaml` is what the daemon reads at admission.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import until
from .helpers import hold_requests, stored_config

CHANGED = re.compile(r"\bsf-changed\b")
GATE_TEST = re.compile(r"/api/routines/uir/gate/test$")


def _gate(page):
    """The Run gate section's panel — addressed by its stable anchor."""
    panel = page.locator("#sec-run-gate + .panel")
    expect(panel).to_be_visible()
    return panel


def test_the_gate_is_visible_off_and_says_how_it_works(ui, ui_page):
    """Discoverable while off, in the group that opens on arrival — and it names the file a
    custom check lives in, which is the file the daemon actually runs."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _gate(ui_page)
    expect(panel).to_contain_text("scripts/admit.py")
    expect(panel.locator("[data-gate-enabled]")).not_to_be_checked()
    expect(panel.locator("[data-gate-enabled]")).to_be_disabled()   # nothing to ask yet
    expect(panel).to_contain_text("no checks — every scheduled fire starts a run")
    expect(panel.locator("[data-gate-test]")).to_be_disabled()


def test_adding_a_check_switches_the_gate_on_and_the_accept_saves_it(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _gate(ui_page)
    panel.locator("[data-gate-add]").select_option("weekdays")
    check = panel.locator('[data-gate-check="weekdays"]')
    expect(check).to_be_visible()
    # the first check switches the gate on — an enabled gate with nothing to ask is refused
    expect(panel.locator("[data-gate-enabled]")).to_be_checked()
    days = check.locator(".gate-param", has_text="days").locator("input")
    days.fill("0, 1, 2, 3, 4, 5, 6")
    days.press("Tab")
    expect(ui_page.locator('.sf-field[data-field="run_gate"]')).to_have_class(CHANGED)
    assert not (stored_config(ui).get("run_gate") or {}).get("checks")  # a draft until accepted

    ui_page.locator(".accept-bar [data-accept]").click()
    until(lambda: (stored_config(ui).get("run_gate") or {}).get("enabled") is True,
          what="the accepted gate")
    gate = stored_config(ui)["run_gate"]
    assert gate["checks"] == [{"kind": "weekdays", "id": "c1", "days": [0, 1, 2, 3, 4, 5, 6]}]
    assert gate["timeout_s"] == 30

    ui_page.reload()
    panel = _gate(ui_page)
    expect(panel.locator("[data-gate-enabled]")).to_be_checked()
    expect(panel.locator('[data-gate-check="weekdays"] .gate-param input')).to_have_value(
        "0, 1, 2, 3, 4, 5, 6")


def test_the_gate_is_tested_as_shown_without_starting_a_run(ui, ui_page):
    """The test asks the checks on the page — not the saved gate — through the admission path
    a fire takes. It says what a fire now would do; no run starts."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _gate(ui_page)
    panel.locator("[data-gate-add]").select_option("weekdays")
    days = panel.locator('[data-gate-check="weekdays"] .gate-param input')
    days.fill("0, 1, 2, 3, 4, 5, 6")
    days.press("Tab")
    with ui_page.expect_request(lambda r: r.url.endswith("/api/routines/uir/gate/test")) as sent:
        panel.locator("[data-gate-test]").click()
    assert sent.value.post_data_json["run_gate"]["checks"][0]["kind"] == "weekdays"
    verdict = panel.locator("[data-gate-verdict]")
    expect(verdict).to_be_visible()
    expect(verdict).to_have_attribute("data-gate-verdict", re.compile("^(run|skip)$"))
    expect(panel.locator(".gate-result")).to_contain_text("weekdays")
    assert ui.runner.fired == [], "testing a gate must not start a run"


def test_the_timeout_rides_the_same_accept(ui, ui_page):
    """A gate that needs longer than 30s is the reason the field exists; it must survive the
    accept rather than silently reverting to the default."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _gate(ui_page)
    panel.locator("[data-gate-add]").select_option("max_quiet")
    quiet = panel.locator('[data-gate-check="max_quiet"] .gate-param input')
    quiet.fill("3")
    quiet.press("Tab")
    timeout = panel.locator("[data-gate-timeout]")
    timeout.fill("90")
    timeout.press("Tab")
    ui_page.locator(".accept-bar [data-accept]").click()
    until(lambda: (stored_config(ui).get("run_gate") or {}).get("timeout_s") == 90,
          what="the timeout")
    assert stored_config(ui)["run_gate"]["checks"] == [{"kind": "max_quiet", "id": "c1", "days": 3}]


def test_the_routines_own_predicate_is_edited_in_place(ui, ui_page):
    """`script` is the routine's own admission check. Its file is scripts/admit.py, edited below
    its row and saved at once — a file of the routine, not a setting."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _gate(ui_page)
    panel.locator("[data-gate-add]").select_option("script")
    row = panel.locator('[data-gate-check="script"]')
    row.get_by_role("button", name="edit scripts/admit.py").click()
    area = row.locator("textarea.code")
    expect(area).to_have_value(re.compile("Admission check for this routine"))
    row.get_by_role("button", name="save scripts/admit.py").click()
    expect(row).to_contain_text("saved scripts/admit.py")
    until((ui.routines / "uir" / "scripts" / "admit.py").is_file, what="the saved predicate")
    # one script check at most: the kind leaves the add menu once it is listed
    expect(panel.locator('[data-gate-add] option[value="script"]')).to_have_count(0)


def _a_gate_to_test(page):
    panel = _gate(page)
    panel.locator("[data-gate-add]").select_option("weekdays")
    days = panel.locator('[data-gate-check="weekdays"] .gate-param input')
    days.fill("0, 1, 2, 3, 4, 5, 6")
    days.press("Tab")
    return panel


def test_a_double_clicked_gate_test_asks_the_checks_once(ui, ui_page):
    """A check can be a mailbox login, a page fetch or the routine's own script — the button
    rests while its test is out, so a double-click asks them once."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _a_gate_to_test(ui_page)
    held = hold_requests(ui_page, GATE_TEST)
    panel.locator("[data-gate-test]").dblclick()
    until(lambda: held, what="the gate test", page=ui_page)
    ui_page.wait_for_timeout(400)
    assert len(held) == 1, f"{len(held)} gate tests for one click"


def test_only_the_newest_gate_test_paints_its_verdict(ui, ui_page):
    """Edit the gate while a slow test is out and test again: the old test, landing last,
    must not put the old gate's verdict under the new one."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _a_gate_to_test(ui_page)
    held = hold_requests(ui_page, GATE_TEST)
    panel.locator("[data-gate-test]").click()
    until(lambda: len(held) == 1, what="the first gate test", page=ui_page)
    timeout = panel.locator("[data-gate-timeout]")
    timeout.fill("45")
    timeout.press("Tab")                                   # the gate changed; it re-renders
    panel.locator("[data-gate-test]").click()
    until(lambda: len(held) == 2, what="the second gate test", page=ui_page)

    held[1].fulfill(json={"decision": "skip", "reason": "the newer gate", "checks": []})
    verdict = panel.locator("[data-gate-verdict]")
    expect(verdict).to_have_attribute("data-gate-verdict", "skip")
    held[0].fulfill(json={"decision": "run", "reason": "the older gate", "checks": []})
    ui_page.wait_for_timeout(500)
    expect(verdict).to_have_attribute("data-gate-verdict", "skip")
    expect(panel.locator(".gate-result")).to_contain_text("the newer gate")


def test_the_predicate_is_saved_once_and_a_queued_write_is_named(ui, ui_page):
    """A double press wrote scripts/admit.py twice. And while a run is active the PUT is QUEUED
    to the run's end (D78-A) — the note still said "saved", so a gate test the reader then ran
    asked the script as it was before the edit."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = _gate(ui_page)
    panel.locator("[data-gate-add]").select_option("script")
    row = panel.locator('[data-gate-check="script"]')
    row.get_by_role("button", name="edit scripts/admit.py").click()
    expect(row.locator("textarea.code")).to_be_visible()
    puts = []

    def queued(route):                   # what the server answers while a run is active
        if route.request.method != "PUT":
            route.continue_()
            return
        puts.append(route.request.post_data_json["path"])
        ui_page.wait_for_timeout(400)
        route.fulfill(json={"ok": True, "queued": True, "pending": 1})

    ui_page.route(re.compile(r"/api/routines/uir/file$"), queued)
    row.get_by_role("button", name="save scripts/admit.py").dblclick()
    expect(row).to_contain_text("scripts/admit.py is written when the active run ends")
    expect(row).not_to_contain_text("saved scripts/admit.py")
    ui_page.wait_for_timeout(400)
    assert puts == ["scripts/admit.py"], f"one press, {len(puts)} writes"
