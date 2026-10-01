"""Settings saves: one request per press, and a save that lands with problems says so.

Fifteen Settings buttons wrote the `try { await api(…); toast(ok) } catch …` shape out by hand
without disabling themselves, so a double click sent the save twice (a second POST of the same
endpoint, a second DELETE that answers 404). They now run through util.js `act()`. And the
config-block saves (endpoints, models, machines) answer `{ok, problems}` — the write landed and
`problems` is what the loader now says about the whole config — which the endpoint and model
saves ignored, reading as plain success (settings-common.js `savedToast`).
"""

from __future__ import annotations

import yaml
from playwright.sync_api import expect


def _held(page, pattern, method):
    """Hold every `method` request to `pattern` for a beat, counting them."""
    seen = []

    def handle(route):
        if route.request.method == method:
            seen.append(route.request.url)
            page.wait_for_timeout(400)
        route.continue_()

    page.route(pattern, handle)
    return seen


def test_a_double_clicked_save_sends_once(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/settings?section=server")
    save = ui_page.get_by_role("button", name="save server settings")
    expect(save).to_be_visible()
    seen = _held(ui_page, "**/api/settings/server", "PUT")
    save.dblclick()
    expect(ui_page.locator("#toast")).to_contain_text("server settings saved")
    ui_page.wait_for_timeout(500)
    assert len(seen) == 1, f"one press, {len(seen)} saves: {seen}"


def test_a_double_clicked_delete_asks_once(ui, ui_page):
    """The confirm-then-delete shape: the button stays disabled while its dialog is open, so a
    second activation is one question, not two stacked dialogs and two DELETEs. Two `click()`
    calls in one task, because a pointer's second click lands on the first dialog's scrim."""
    ui_page.goto(f"{ui.url}/#/settings?section=secrets")
    ui_page.get_by_placeholder("KEY (e.g. CLAUDE_CODE_OAUTH_TOKEN)").fill("DOUBLE_PROBE")
    value = ui_page.locator('textarea[placeholder="value"]')
    value.fill("x")
    value.locator("xpath=..").get_by_role("button", name="set", exact=True).click()
    row = ui_page.locator("tr", has_text="DOUBLE_PROBE")
    row.get_by_role("button", name="delete").evaluate("b => { b.click(); b.click(); }")
    expect(ui_page.get_by_role("dialog")).to_have_count(1)
    ui_page.get_by_role("dialog").get_by_role("button", name="delete").click()
    expect(ui_page.locator("tr", has_text="DOUBLE_PROBE")).to_have_count(0)


def test_a_model_save_that_lands_with_problems_says_so(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator(".panel", has_text="dummy / m").last
    expect(card).to_be_visible()
    # a hand edit the loader will now complain about; every block save re-reads the whole file
    path = ui.tmp / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["compaction_model"] = "ghost"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    card.locator("summary", has_text="edit fields").click()
    card.get_by_role("button", name="save changes").click()
    toast = ui_page.locator("#toast")
    expect(toast).to_contain_text("m: updated — but the config now reports:")
    expect(toast).to_contain_text("compaction_model: 'ghost' is not a catalog model")
    expect(toast).to_have_class("err")
