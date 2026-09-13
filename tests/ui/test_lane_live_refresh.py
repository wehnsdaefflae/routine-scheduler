"""Run transitions refresh live lane state without reloading domain configuration."""
import json

from playwright.sync_api import expect

from rsched import lanes


def test_lane_progress_tracks_run_transitions_without_domain_refetch(ui, ui_page, make_routine):
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
    ui_page.on("request", lambda request: calls.append("domains")
               if request.url.endswith("/api/domains") else None)
    ui_page.goto(f"{ui.url}/#/routines")
    row = ui_page.locator(f'tr[data-lane-row="{lane["id"]}"]')
    expect(row).to_be_visible(timeout=10000)
    expect(row.locator("[data-lane-run]")).to_be_enabled()
    domain_calls = calls.count("domains")

    def transition(event):
        ui_page.evaluate("event => window.dispatchEvent(new CustomEvent('rsched-bus', {detail:{event}}))", event)

    flight[lane["id"]] = {"cursor": 0, "members": lane["members"]}
    transition("run_started")
    expect(row.locator("[data-lane-progress]")).to_contain_text("1/2", timeout=10000)
    expect(row.locator("[data-lane-run]")).to_be_disabled()
    flight[lane["id"]]["cursor"] = 1
    transition("run_state")
    expect(row.locator("[data-lane-progress]")).to_contain_text("2/2", timeout=10000)
    flight.clear()
    transition("run_finished")
    expect(row.locator("[data-lane-progress]")).to_have_count(0, timeout=10000)
    expect(row.locator("[data-lane-run]")).to_be_enabled()
    assert calls.count("domains") == domain_calls
    assert calls.count("lanes") >= 4
