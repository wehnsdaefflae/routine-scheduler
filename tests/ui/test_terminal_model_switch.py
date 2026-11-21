"""F837 — a run that DIED on its model can be re-pointed, from its own page."""

from __future__ import annotations

import json

from playwright.sync_api import expect

RUN = "20261105-010000"


def test_a_finished_run_still_offers_the_model_switch(ui, ui_page):
    """The control is reachable, and the API accepts it — the two halves of the same fix."""
    run_dir = ui.seed_run("uitms", RUN, "finished", summary="died on its model")
    ui_page.goto(f"{ui.url}/#/run/uitms:{RUN}")

    switch = ui_page.locator("details", has_text="switch model / effort")
    expect(switch).to_be_visible()
    # the resume control is there too: the two belong together — re-point, then resume
    expect(ui_page.locator("button", has_text="resume run")).to_be_visible()

    switch.locator("summary").click()
    sel = switch.locator('select[aria-label="main model"]')
    expect(sel).to_be_visible()
    name = sel.locator("option").first.inner_text()
    sel.select_option(name)
    switch.locator("button", has_text="switch").click()

    # the toast must not promise a turn that will never come
    expect(ui_page.locator(".toast, #toast")).to_contain_text("resume")
    # …and the switch is really on disk, where the resumed leg's first boundary reads it
    ctrl = json.loads((run_dir / "control.json").read_text(encoding="utf-8"))
    assert ctrl["switch_model"]["main"] == name
