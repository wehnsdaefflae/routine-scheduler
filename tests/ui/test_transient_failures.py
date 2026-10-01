"""A read that failed once is asked again — it is not the answer.

Two components remembered a FAILED fetch for the life of the tab: the full-description toggle
(components/docexpand.js) marked its doc loaded before the read and kept the error text on
every later open, and the run-gate editor (components/gate-editor.js) cached the rejected
promise of its check vocabulary, so one refused read broke every gate editor until a reload.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import expect


def _unfold(page) -> None:
    """Open every routine-page settings group and each group's "more" menu (the page ships
    with only its two leading groups open — views/routine-config.js)."""
    page.wait_for_selector(".rgroup-head")
    page.evaluate("() => { for (const d of document.querySelectorAll('details.rgroup, details.rmore')) d.open = true; }")


def _fails_once(page, pattern):
    """Answer the first request matching `pattern` with a 503; let every later one through."""
    calls: list[str] = []

    def handle(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(status=503, content_type="application/json",
                          body=json.dumps({"detail": "busy, try again"}))
        else:
            route.fallback()

    page.route(pattern, handle)
    return calls


def test_a_failed_doc_read_is_asked_again_on_the_next_open(ui, ui_page):
    _fails_once(ui_page, re.compile(r"/api/library/permissions/[^/?]+$"))
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _unfold(ui_page)
    card = ui_page.locator('#sec-permissions + .panel .ability[data-ability="util-authoring"]')
    toggle = card.get_by_role("button", name="full description")
    body = card.locator(".doc-expand-body")

    toggle.click()
    expect(body).to_contain_text("could not load")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()                                       # the next open asks again
    expect(body).to_contain_text("one subject, one util")


def test_a_failed_gate_vocabulary_read_is_asked_again_on_the_next_visit(ui, ui_page):
    _fails_once(ui_page, re.compile(r"/api/gate/kinds$"))
    ui_page.goto(f"{ui.url}/#/routine/uir")
    gate = ui_page.locator("#sec-run-gate + .panel")
    expect(gate).to_contain_text("could not load the gate's check vocabulary")

    ui_page.evaluate("location.hash = '#/help'")        # leave, in-page: the module stays
    ui_page.wait_for_selector("#view h1")
    ui_page.evaluate("location.hash = '#/routine/uir'")
    expect(ui_page.locator("#sec-run-gate + .panel [data-gate-add]")).to_be_visible()
