"""The 'Recommended setup' panel on the routine page — the inverse of the setup surface,
rendered where somebody will act on it. The recommender itself is one system-model pass and the
UI harness has no live model, so the response is stubbed at the network with Playwright routing:
what is under test is the panel — the button, the fetch, and how a verdict list renders."""
from __future__ import annotations

import json

from playwright.sync_api import expect


def _stub(ui_page, body: dict) -> None:
    ui_page.route(
        "**/api/routines/uir/recommendations",
        lambda route: route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps(body)))


def test_recommend_lists_only_the_changes_with_reasons(ui, ui_page):
    _stub(ui_page, {"available": True, "items": [
        {"slug": "web-research", "kind": "rule", "held": False, "recommend": True,
         "reason": "it looks up release notes online"},
        {"slug": "memory", "kind": "permission", "held": True, "recommend": False,
         "reason": "no cross-run state is needed"},
        {"slug": "ask-policy", "kind": "rule", "held": True, "recommend": True,
         "reason": "already aligned"},
    ]})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Recommended setup')", timeout=10_000)
    ui_page.locator(".recommend-panel button").click()

    add = ui_page.locator('.rec-add .rec-row[data-slug="web-research"]')
    add.wait_for(state="visible", timeout=10_000)
    assert "release notes" in add.inner_text()
    assert "rule" in add.inner_text()
    drop = ui_page.locator('.rec-drop .rec-row[data-slug="memory"]')
    assert drop.count() == 1
    assert "no cross-run state" in drop.inner_text()
    # an already-aligned item is a count, never a row (a set that matches should not read as a
    # wall of things to re-confirm)
    assert ui_page.locator('.rec-row[data-slug="ask-policy"]').count() == 0
    expect(ui_page.locator(".recommend-out")).to_contain_text("already aligned")


def test_recommend_reports_a_matching_set_as_looks_right(ui, ui_page):
    _stub(ui_page, {"available": True, "items": [
        {"slug": "ask-policy", "kind": "rule", "held": True, "recommend": True, "reason": "x"},
        {"slug": "shell", "kind": "permission", "held": False, "recommend": False, "reason": "y"},
    ]})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Recommended setup')", timeout=10_000)
    ui_page.locator(".recommend-panel button").click()
    aligned = ui_page.locator("[data-rec-aligned]")
    aligned.wait_for(state="visible", timeout=10_000)
    assert "Looks right" in aligned.inner_text()


def test_recommend_degrades_when_no_model_answers(ui, ui_page):
    _stub(ui_page, {"available": False, "items": []})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Recommended setup')", timeout=10_000)
    ui_page.locator(".recommend-panel button").click()
    expect(ui_page.locator(".recommend-panel")).to_contain_text("unavailable", timeout=10_000)


def test_recommend_dropped_connection_reads_honestly(ui, ui_page):
    """The recommend pass is a slow system-model read, so its connection can be dropped by a
    proxy timeout or a deploy. That surfaces as a bare browser 'NetworkError…'; the panel must
    say what happened and what to do (retry; the panels below still work), never leak the raw
    string (operator screenshot 2026-09-03)."""
    ui_page.route("**/api/routines/uir/recommendations", lambda route: route.abort())
    ui_page.goto(f"{ui.url}/#/routine/uir")
    ui_page.wait_for_selector("h2:has-text('Recommended setup')", timeout=10_000)
    ui_page.locator(".recommend-panel button").click()
    out = ui_page.locator(".recommend-out")
    expect(out).to_contain_text("didn't answer in time", timeout=10_000)
    expect(out).to_contain_text("panels below")
    assert "NetworkError" not in out.inner_text()
