"""The run view's two controls that act on the RUN rather than on its settings
(`static/components/runsupervise.js`): 👁 supervise a live run, ⟲ rewind a terminal one.

Their own file because they are one surface concern and `test_flows.py` is 2,127 lines against
this repo's 350-line bar — and because the pair's VISIBILITY is the contract: each is offered in
exactly the state where it can work, so they are asserted together or not at all.
"""

import json

from playwright.sync_api import expect


def test_run_view_supervise_button_asks_a_live_run_for_a_rung(ui, ui_page):
    """F750: the operator pressed run-now with the brief "rung" while `fau` was live and got
    "routine fau is busy" (two toasts, 2026-10-16). The daemon rightly refuses a second run of a
    routine — `runner.active` is keyed by slug and two would race its state — so supervising a
    run in flight is a REQUEST ON THE RUN: the ladder honours it at the next turn boundary as an
    in-process oversight child. The live run's view offers 👁 supervise, which POSTs /supervise;
    it is the mirror image of ⟲ rewind beside it, shown while live and hidden once terminal.
    """
    ui.seed_run("uis", "20260716-080000", "running")

    posted = {}

    def handle(route):
        posted["hit"] = True
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True,
                                       "supervise": "a rung runs at this run's next "
                                                    "turn boundary"}))
    ui_page.route("**/api/runs/uis:20260716-080000/supervise", handle)

    ui_page.goto(f"{ui.url}/#/run/uis:20260716-080000")
    supervise = ui_page.get_by_role("button", name="👁 supervise")
    expect(supervise).to_be_visible()                      # live run → the control is offered
    expect(ui_page.get_by_role("button", name="⟲ rewind")).to_be_hidden()   # …and rewind is not
    supervise.click()
    # The POST is the contract, as for ⟲ rewind: the success toast is transient and asserting
    # its visibility races its own timeout.
    ui_page.wait_for_timeout(400)
    assert posted.get("hit"), "the supervise button did not reach the endpoint"


def test_a_terminal_run_offers_rewind_and_not_supervise(ui, ui_page):
    """The other half of the pair: once a run is terminal there is no turn boundary left for a
    rung to run at (the endpoint refuses one too), while rewind becomes the remedy on offer.
    Asserted here because the two controls share a component and a visibility rule.
    """
    ui.seed_run("uit", "20260716-090000", "failed", summary="died on overflow")

    ui_page.goto(f"{ui.url}/#/run/uit:20260716-090000")
    expect(ui_page.get_by_role("button", name="⟲ rewind")).to_be_visible()
    expect(ui_page.get_by_role("button", name="👁 supervise")).to_be_hidden()
