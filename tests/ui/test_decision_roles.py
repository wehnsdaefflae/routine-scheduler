"""A routine's — and a conversation's — own decision models, in a browser: the routine page's
Models group offers the two decision roles once the instance has decision models (the image
role only the models that take images), and the choice rides the one accept to routine.yaml;
a conversation's header carries the decide picker and keeps the image role through its save."""

import yaml
from playwright.sync_api import expect

from rsched.config.decisionconf import DecisionEndpointConfig, DecisionModelConfig

from .conftest import until
from .helpers import configure, start_conversation


def _with_decision_models(ui) -> None:
    server = ui.server_cfg
    server.decision_endpoints = {
        "predator": DecisionEndpointConfig(name="predator", protocol="openai",
                                           base_url="http://box:8790/v1"),
        "jev": DecisionEndpointConfig(name="jev", protocol="systemone")}
    server.decision_models = {
        "vl8b": DecisionModelConfig(name="vl8b", endpoint="predator", model="qwen3-vl"),
        "jev": DecisionModelConfig(name="jev", endpoint="jev", model="jev-latest")}
    server.decision_model, server.decision_media_model = "jev", "vl8b"


def _stored(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _open_models(page) -> None:
    page.wait_for_selector(".rgroup-head")
    page.evaluate("() => { for (const d of document.querySelectorAll('details.rgroup')) "
                  "if (d.dataset.group === 'Models') d.open = true; }")


def test_no_decision_models_means_no_decision_rows(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_models(ui_page)
    expect(ui_page.locator('select[data-model-role="main"]')).to_be_visible()
    expect(ui_page.locator('select[data-model-role="decision"]')).to_have_count(0)


def test_a_routine_picks_its_decision_models(ui, ui_page):
    _with_decision_models(ui)
    configure(ui, models={"main": "m"})
    path = ui.routine_dir("uir") / "routine.yaml"
    ui_page.goto(f"{ui.url}/#/routine/uir")
    _open_models(ui_page)
    text = ui_page.locator('select[data-model-role="decision"]')
    media = ui_page.locator('select[data-model-role="decision_media"]')
    expect(text.locator("option").first).to_have_text("— instance default (jev) —")
    expect(text.locator("option")).to_have_count(3)                 # blank + both models
    expect(media.locator("option")).to_have_count(2)                # blank + the one that sees
    expect(media.locator("option").nth(1)).to_have_text("vl8b · text + images")
    text.select_option("vl8b")
    with ui_page.expect_request(lambda r: r.method == "POST"
                                and r.url.endswith("/api/routines/uir/settings")) as sent:
        ui_page.locator(".accept-bar [data-accept]").click()
    assert sent.value.post_data_json["changes"]["models"] == {"main": "m", "decision": "vl8b"}
    until(lambda: _stored(path).get("models", {}).get("decision") == "vl8b",
          what="the accepted decision role")
    assert _stored(path)["models"]["main"] == "m"                   # the chat role kept


def test_a_conversation_header_carries_the_decide_picker(ui, ui_page):
    _with_decision_models(ui)
    slug, conv = start_conversation(ui, ui_page, "classify some tickets")
    configure_conv = conv / "routine.yaml"
    raw = _stored(configure_conv)
    raw["models"] = {**(raw.get("models") or {}), "decision_media": "vl8b"}
    configure_conv.write_text(yaml.safe_dump(raw), encoding="utf-8")
    ui_page.reload()
    picker = ui_page.locator('.conv-model select[data-model-role="decision"]')
    expect(picker).to_be_visible()
    picker.select_option("jev")
    with ui_page.expect_request(lambda r: r.method == "PATCH"
                                and r.url.endswith(f"/api/conversations/{slug}")) as sent:
        ui_page.locator(".conv-model").get_by_role("button", name="apply").click()
    models = sent.value.post_data_json["models"]
    assert models["decision"] == "jev"
    assert models["decision_media"] == "vl8b"                       # carried through untouched
    until(lambda: _stored(configure_conv).get("models", {}).get("decision") == "jev",
          what="the saved decide picker")
