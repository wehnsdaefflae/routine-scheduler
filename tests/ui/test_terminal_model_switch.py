"""F837 — a run that DIED on its model can be re-pointed, from its own page.

Operator, 2026-11-05: *"you cannot switch the model if the run is paused or aborted. but
sometimes i have to"*. Measured that day: a `paused` run always could (it is in
`registry.ACTIVE_STATES`, and `loop._turn_boundary` runs the pause gate BEFORE
`apply_model_switch`), but `finished`/`failed`/`aborted` were refused at BOTH layers — a 409
from `require_active` and the control hidden by `setState`'s `switchBox.hidden = terminal`.
That is exactly the run a person needs to re-point: `deficit-coach` died three times on
2026-11-04 with `model_chain_exhausted` after 0 rungs, every one an Opus 429, and neither
`resume-run` nor `rewind` takes a model — so it could only be restarted onto the model that
had just failed it.

Asserted here as A READER SEES IT (the house rule in `test_turn_model.py`): the control is on
the page of a finished run, and the toast says the switch lands at the RESUME rather than
"next turn", because a control promising a turn that will never come is how a person concludes
it did nothing.
"""

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
