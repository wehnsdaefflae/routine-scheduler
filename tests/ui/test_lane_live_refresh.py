"""Run transitions refresh live lane state without re-reading the config-shaped week schedule."""
import json

from playwright.sync_api import expect

from rsched import lanes


def test_lane_progress_tracks_run_transitions_without_schedule_refetch(ui, ui_page,
                                                                      make_routine):
    make_routine(slug="uir2")
    lane = lanes.create(ui.routines, name="Live chain", members=[{"slug": "uir"}, {"slug": "uir2"}])
    flight = {}
    calls = []

    def lane_response(route):
        response = route.fetch()
        body = response.json()
        body["in_flight"] = dict(flight)
        calls.append("lanes")
        route.fulfill(response=response, body=json.dumps(body))

    ui_page.route("**/api/lanes", lane_response)
    ui_page.on("request", lambda request: calls.append("week")
               if "/api/schedule/week" in request.url else None)
    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'tr[data-lane-row="{lane["id"]}"]')
    expect(row).to_be_visible()
    expect(row.locator("[data-lane-run]")).to_be_enabled()
    week_calls = calls.count("week")

    def transition(event):
        ui_page.evaluate("event => window.dispatchEvent(new CustomEvent('rsched-bus', {detail:{event}}))", event)

    flight[lane["id"]] = {"cursor": 0, "members": lane["members"]}
    transition("run_started")
    expect(row.locator("[data-lane-progress]")).to_contain_text("1/2")
    expect(row.locator("[data-lane-run]")).to_be_disabled()
    flight[lane["id"]]["cursor"] = 1
    transition("run_state")
    expect(row.locator("[data-lane-progress]")).to_contain_text("2/2")
    flight.clear()
    transition("run_finished")
    expect(row.locator("[data-lane-progress]")).to_have_count(0)
    expect(row.locator("[data-lane-run]")).to_be_enabled()
    assert calls.count("week") == week_calls
    assert calls.count("lanes") >= 4
