"""Reasoning EFFORT as a setting of its own, in a browser.

The routine page's Models group puts an effort picker beside each role's model and the choice
rides the ONE accept to `efforts:` in routine.yaml; the conversation composer and header carry
the same pickers; the run page's switch changes the effort of a live run without touching its
model. One catalog model serves every level — none of these needs a row per level.
"""

import json
import re

import yaml
from playwright.sync_api import expect

from .conftest import until
from .helpers import accept, configure, start_conversation, stored_config, unfold


def _levels(select) -> list[str]:
    return [o.get_attribute("value") for o in select.locator("option").all()]


def test_the_routine_page_offers_each_role_an_effort_beside_its_model(ui, ui_page):
    configure(ui, models={"main": "m"})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    main = ui_page.locator('select[data-effort-role="main"]')
    expect(main).to_be_visible()
    assert _levels(main) == ["", "low", "medium", "high", "xhigh", "max"]
    expect(main.locator("option").first).to_have_text("effort: provider default")
    for role in ("tool_call", "uncensored"):
        expect(ui_page.locator(f'select[data-effort-role="{role}"]')).to_be_visible()


def test_an_effort_is_accepted_into_efforts_and_leaves_the_models_alone(ui, ui_page):
    configure(ui, models={"main": "m"})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    ui_page.locator('select[data-effort-role="main"]').select_option("xhigh")
    ui_page.locator('select[data-effort-role="tool_call"]').select_option("low")
    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/routines/uir/settings")) as sent:
        accept(ui_page)
    changes = sent.value.post_data_json["changes"]
    assert changes["efforts"] == {"main": "xhigh", "tool_call": "low"}
    assert "models" not in changes                       # no catalog row was needed or touched
    until(lambda: stored_config(ui).get("efforts") == {"main": "xhigh", "tool_call": "low"},
          what="the accepted efforts")
    assert stored_config(ui)["models"] == {"main": "m"}
    # and a reload shows what was kept
    ui_page.reload()
    unfold(ui_page)
    expect(ui_page.locator('select[data-effort-role="main"]')).to_have_value("xhigh")


def test_a_cleared_effort_returns_the_role_to_the_provider_default(ui, ui_page):
    configure(ui, models={"main": "m"}, efforts={"main": "high"})
    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    picker = ui_page.locator('select[data-effort-role="main"]')
    expect(picker).to_have_value("high")
    picker.select_option("")
    accept(ui_page)
    until(lambda: not stored_config(ui).get("efforts"), what="the cleared effort")


def test_the_composer_sends_efforts_with_the_new_conversation(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.wait_for_selector(".conv-setup", state="attached")
    ui_page.evaluate("() => { document.querySelector('.conv-setup').open = true; }")
    ui_page.locator('.conv-setup select[data-effort-role="main"]').select_option("high")
    ui_page.locator(".conv-new textarea").fill("think hard about this")
    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/conversations")) as sent:
        ui_page.get_by_role("button", name="start conversation").click()
    body = sent.value.post_data or ""
    assert re.search(r'name="efforts"\r\n\r\n(\{[^}]*\})', body), body
    efforts = json.loads(re.search(r'name="efforts"\r\n\r\n(\{[^}]*\})', body).group(1))
    assert efforts == {"main": "high"}
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    saved = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert saved["efforts"] == {"main": "high"}


def test_the_conversation_header_changes_effort_without_a_model_change(ui, ui_page):
    slug, conv = start_conversation(ui, ui_page, "a conversation to raise the effort of")
    picker = ui_page.locator('.conv-model select[title="main reasoning effort"]')
    expect(picker).to_be_visible()
    picker.select_option("max")
    with ui_page.expect_request(lambda r: r.method == "PATCH"
                                and r.url.endswith(f"/api/conversations/{slug}")) as sent:
        ui_page.locator(".conv-model").get_by_role("button", name="apply").click()
    assert sent.value.post_data_json["efforts"] == {"main": "max"}
    until(lambda: yaml.safe_load((conv / "routine.yaml").read_text()).get("efforts")
          == {"main": "max"}, what="the saved conversation effort")


def test_a_live_run_is_switched_to_another_effort_not_another_model(ui, ui_page):
    ui.seed_run("uir", "20261014-120000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20261014-120000")
    ui_page.locator("details", has_text="switch model / effort").locator("summary").click()
    picker = ui_page.locator('select[data-effort-role="main"]')
    expect(picker).to_be_visible()
    expect(picker.locator("option").first).to_have_text("effort: keep")
    picker.select_option("high")
    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/runs/uir:20261014-120000/model")) as sent:
        ui_page.get_by_role("button", name="switch", exact=True).click()
    assert sent.value.post_data_json["effort"] == "high"
