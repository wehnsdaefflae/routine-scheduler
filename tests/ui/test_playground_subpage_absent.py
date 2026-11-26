"""F959 — the Playground console subpage is gone, proved in the BROWSER, flag ON. The operator's
order (2026-11-25) was to remove the subpage; the flag `playground_enabled` was that page's only
gate, so it went with it — which is exactly what makes a browser proof necessary: an instance
whose config.yaml still says `playground_enabled: true` (and any cached console asking for the
key) must get NO navigation entry and NO page, so these tests serve the status payload with the
key forced on, the strongest form of the claim. What must keep working is asserted in the same
file: Conversations, and a stored run still readable — the removal took a console subpage, not
anyone's history.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import expect

STATUS = re.compile(r"/api/status$")


def _status_with_the_flag_on(ui_page):
    """Serve the REAL /api/status with `playground_enabled: true` merged in."""
    def handler(route):
        body = route.fetch().json()
        body["playground_enabled"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui_page.route(STATUS, handler)


def test_no_playground_navigation_even_with_the_flag_on(ui, ui_page):
    _status_with_the_flag_on(ui_page)
    ui_page.goto(f"{ui.url}/#/conversations")
    # the nav itself rendered (so a count of 0 below means absence, not an unpainted rail)
    expect(ui_page.locator("#nav a[data-nav=conversations]")).to_have_count(1)
    expect(ui_page.locator("#nav a[data-nav=questions]")).to_have_count(1)
    # ...and the Playground entry exists in no form: by id, by data-nav, by href, by label
    expect(ui_page.locator("#nav-playground")).to_have_count(0)
    expect(ui_page.locator("#nav a[data-nav=playground]")).to_have_count(0)
    expect(ui_page.locator('#nav a[href="#/playground"]')).to_have_count(0)
    expect(ui_page.get_by_role("link", name="Playground")).to_have_count(0)


def test_the_legacy_hash_loads_no_playground_page(ui, ui_page):
    _status_with_the_flag_on(ui_page)
    ui_page.goto(f"{ui.url}/#/playground")
    # an unrouted hash falls back to the landing page: the composer is the proof it rendered
    expect(ui_page.locator(".conv-new textarea")).to_have_count(1)
    expect(ui_page.locator("[data-nav=conversations]")).to_have_class(re.compile(r"\bactive\b"))
    # nothing of the page: no heading, no bench panel, no "view failed to load" stub either
    expect(ui_page.get_by_role("heading", name=re.compile("Playground"))).to_have_count(0)
    expect(ui_page.locator(".empty .t", has_text="view failed to load")).to_have_count(0)
    assert "playground" not in ui_page.inner_text("body").lower()
    assert not ui_page.url.endswith("#/playground")      # the router sent it home


def test_conversations_and_a_stored_run_still_work(ui, ui_page):
    """The removal is a page, not a history: a run that happened is still readable. What is
    asserted is what the run page RENDERS for a stored run — its header and its terminal state;
    `seed_run`'s summary goes to the run's `result.md`, which this page does not print into the
    body, so asserting on that would be asserting on a file rather than on the view."""
    _status_with_the_flag_on(ui_page)
    ui.seed_run("uir", "20260730-070000", "finished", summary="still here after F959")

    ui_page.goto(f"{ui.url}/#/conversations")
    expect(ui_page.locator(".conv-new textarea")).to_have_count(1)

    ui_page.goto(f"{ui.url}/#/run/uir:20260730-070000")
    expect(ui_page.locator("#crumbs")).to_contain_text("uir")
    expect(ui_page.locator("body")).to_contain_text("run 2026-07-30 07:00")
    expect(ui_page.locator("body")).to_contain_text("finished")
