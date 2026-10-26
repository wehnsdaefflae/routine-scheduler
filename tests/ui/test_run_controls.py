"""The run view's two controls that act on the RUN rather than on its settings
(`static/components/runsupervise.js`): 👁 supervise a live run, ⟲ rewind a terminal one.

Their own file because they are one surface concern and `test_flows.py` is 2,127 lines against
this repo's 350-line bar — and because the pair's VISIBILITY is the contract: each is offered in
exactly the state where it can work, so they are asserted together or not at all.
"""

import json

from playwright.sync_api import expect

from .helpers import hold_requests


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


def test_supervise_is_dead_while_in_flight_and_hands_focus_back_after(ui, ui_page):
    """F779: the two controls run through util.js `act()` now, and these are its observable
    promises — the ones the hand-rolled shape could not keep.

    `act` disables for the whole in-flight window (a double-click cannot send the ask twice),
    re-enables in a `finally` (the only way it cannot be forgotten — the hand copy re-enabled
    on both paths only because nothing between the await and the re-enable threw, and
    `static/util.js:337` names that failure: its author saw the control stay dead for real when
    a missing toast host made `toast()` throw), and hands focus BACK to the button that had it,
    which a disabled button cannot hold — so a keyboard reader is not dropped onto <body>.
    """
    ui.seed_run("uithrow", "20260716-081500", "running")
    held = hold_requests(ui_page, "**/api/runs/uithrow:20260716-081500/supervise")

    ui_page.goto(f"{ui.url}/#/run/uithrow:20260716-081500")
    supervise = ui_page.get_by_role("button", name="👁 supervise")
    expect(supervise).to_be_visible()
    supervise.focus()
    supervise.click()
    expect(supervise).to_be_disabled()          # in flight: the ask cannot be sent twice
    assert held, "the supervise button did not reach the endpoint"

    held[0].fulfill(status=200, content_type="application/json",
                    body=json.dumps({"ok": True, "supervise": "a rung runs next boundary"}))
    expect(supervise).to_be_enabled()           # the `finally` re-enable
    expect(supervise).to_be_focused()           # …and the focus `act` hands back


def test_rewind_comes_back_to_life_after_a_refused_rewind(ui, ui_page):
    """The second failure mode the helper's docstring names, which ⟲ rewind had: it re-enabled
    ONLY inside its `catch`. A rewind the server refuses must leave the operator able to try
    another turn number — and on the success path the page remounts, so the button goes with it.
    """
    ui.seed_run("uirw", "20260716-091500", "failed", summary="died on overflow")
    ui_page.route("**/api/runs/uirw:20260716-091500/rewind",
                  lambda route: route.fulfill(
                      status=409, content_type="application/json",
                      body=json.dumps({"detail": "turn 9 is past the transcript"})))

    ui_page.goto(f"{ui.url}/#/run/uirw:20260716-091500")
    rewind = ui_page.get_by_role("button", name="⟲ rewind")
    expect(rewind).to_be_visible()
    rewind.click()
    ui_page.get_by_placeholder("turn number").fill("9")
    ui_page.get_by_role("button", name="OK").click()
    expect(rewind).to_be_enabled()
    # And the keyboard reader is not dropped: the dialog hands focus back to its opener on
    # close, the button is disabled a moment later (so it loses it), and only `act`'s `finally`
    # gives it back. The hand-rolled version re-enabled in its `catch` and left focus on <body>.
    expect(rewind).to_be_focused()


def test_a_terminal_run_offers_rewind_and_not_supervise(ui, ui_page):
    """The other half of the pair: once a run is terminal there is no turn boundary left for a
    rung to run at (the endpoint refuses one too), while rewind becomes the remedy on offer.
    Asserted here because the two controls share a component and a visibility rule.
    """
    ui.seed_run("uit", "20260716-090000", "failed", summary="died on overflow")

    ui_page.goto(f"{ui.url}/#/run/uit:20260716-090000")
    expect(ui_page.get_by_role("button", name="⟲ rewind")).to_be_visible()
    expect(ui_page.get_by_role("button", name="👁 supervise")).to_be_hidden()
