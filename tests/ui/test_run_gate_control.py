"""The run gate has a switch in the console, beside the schedule (D141).

The gate (F457) has been wired into admission for some time — `daemon/run_gate.admit` runs
before the engine subprocess is created, and `PATCH /api/routines/{slug}` always accepted
`run_gate`. What never existed was a way to SEE or SET it without hand-editing
`routine.yaml`: a feature built to save budget was reachable only by someone who already knew
it was there, and a routine silently gated off looked exactly like a routine that was broken.

The operator chose the placement (2026-09-21): *"On the config page beside the schedule"* — a
gate decides WHETHER a scheduled fire becomes a run, so it belongs with WHEN it fires, and it
rides the Schedule section's own save rather than growing a second button.

This drives the real console: tick the box, save, and the file on disk is the proof, because
that file is what the daemon reads at admission time. The controls carry
`data-run-gate` / `data-run-gate-timeout` hooks — the config page is dense with checkboxes
and number fields, and an index-based locator here silently asserts about another setting.
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect


def _stored(ui, slug="uir"):
    return yaml.safe_load(
        (ui.routines / slug / "routine.yaml").read_text(encoding="utf-8"))


def _schedule_section(ui_page):
    """The Schedule panel, located by its own save button — the way the rest of this suite
    finds a config panel. The page renders every section on `#/routine/<slug>`; there is no
    tab parameter, and an id selector would depend on settingsSection's internals.
    """
    section = ui_page.locator(
        ".panel", has=ui_page.get_by_role("button", name="save schedule"))
    expect(section).to_be_visible(timeout=15_000)
    return section


def test_the_gate_is_off_and_visible_in_the_schedule_section(ui, ui_page):
    """Discoverable while off — the whole point of giving it a surface."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    section = _schedule_section(ui_page)
    expect(section).to_contain_text("run gate")
    expect(section).to_contain_text("scripts/run_gate.py")
    expect(section.locator("[data-run-gate]")).not_to_be_checked()
    # the timeout only matters once the gate is on, so it stays out of the way until then
    expect(section.get_by_text("seconds to answer")).to_be_hidden()


def test_ticking_the_gate_and_saving_persists_it(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    section = _schedule_section(ui_page)
    assert not _stored(ui).get("run_gate", {}).get("enabled")   # precondition: off

    section.locator("[data-run-gate]").check()
    expect(section.get_by_text("seconds to answer")).to_be_visible()
    section.get_by_role("button", name="save schedule").click()
    ui_page.wait_for_timeout(800)

    assert _stored(ui)["run_gate"]["enabled"] is True, _stored(ui)

    # and it reads back as on, so a reload never suggests the save was lost
    ui_page.reload()
    section = _schedule_section(ui_page)
    expect(section.locator("[data-run-gate]")).to_be_checked()


def test_the_gate_timeout_is_saved_with_it(ui, ui_page):
    """A gate that needs longer than 30s is the reason the field exists; it must survive the
    same save rather than silently reverting to the default."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    section = _schedule_section(ui_page)
    section.locator("[data-run-gate]").check()
    section.locator("[data-run-gate-timeout]").fill("90")
    section.get_by_role("button", name="save schedule").click()
    ui_page.wait_for_timeout(800)

    stored = _stored(ui)["run_gate"]
    assert stored == {"enabled": True, "timeout_s": 90}, stored
