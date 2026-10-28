"""A conversation's deliberately chosen tool-call model survives a chat-model switch (F784).

The composer exists to let a conversation run a DIFFERENT tool-call model from its chat model
(`static/views/conversations-new.js`: it seeds `tool_call` from the main picker, then overrides
it when its own tool-call picker is set). The conversation header's model `apply` then sent
`models.tool_call = <main>` on every save, so the first time the operator switched the chat
model that choice was silently gone — the composer's picker undone by the surface next to it.

Nothing records WHICH it was, and nothing needs to: the seed makes a seeded role equal to main,
so a stored `tool_call` that DIFFERS from `main` can only have been chosen. These tests pin
both halves of that rule, plus the case the hand-over did not name — clearing main back to the
system default must not drop a chosen tool role either.
"""

import yaml
from playwright.sync_api import expect

from rsched.config.modelconf import ModelConfig

from .conftest import until
from .helpers import start_conversation


def _two_models(ui, *names: str) -> None:
    """The ui harness ships ONE catalog model, so nothing can be switched TO. Add the ones a
    test needs, each with a window wide enough that modelOption does not disable its option.

    THREE matter for the chosen-role test: with only `m` and `m2`, switching main TO `m2`
    while `m2` is the chosen tool role passes under the DEFECT as well — the clobber writes
    the value the assertion wants. The switch has to land somewhere else (`m3`) for the
    assertion to be able to fail.
    """
    for n in names or ("m2",):
        ui.server_cfg.models[n] = ModelConfig(name=n, endpoint="dummy", model=n,
                                              context_tokens=400_000)


def _main_picker(page):
    """The main/uncensored pickers carry no data-model-role (only `decision` does), so the
    house locator for them is the title — as tests/ui/test_effort_setting.py uses."""
    return page.locator('.conv-model select[title="main model"]')


def _stored(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _set_roles(conv, **roles):
    """Put a role map on the conversation the way the composer would have."""
    path = conv / "routine.yaml"
    raw = _stored(path)
    raw["models"] = {**(raw.get("models") or {}), **roles}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _apply(page, slug):
    with page.expect_request(lambda r: r.method == "PATCH"
                             and r.url.endswith(f"/api/conversations/{slug}")) as sent:
        page.locator(".conv-model").get_by_role("button", name="apply").click()
    return sent.value.post_data_json["models"]


def test_a_chosen_tool_call_model_survives_a_chat_model_switch(ui, ui_page):
    """The defect: the header overwrote tool_call with main on every apply."""
    _two_models(ui, "m2", "m3")
    slug, conv = start_conversation(ui, ui_page, "write some utils for me")
    path = _set_roles(conv, main="m", tool_call="m2")
    ui_page.reload()

    main = _main_picker(ui_page)
    expect(main).to_be_visible()
    main.select_option("m3")                      # switch the CHAT model somewhere ELSE

    models = _apply(ui_page, slug)
    assert models["main"] == "m3"
    assert models["tool_call"] == "m2"            # unchanged: the chosen role, not the new main
    until(lambda: _stored(path).get("models", {}).get("tool_call") == "m2",
          what="the saved tool-call role")


def test_a_seeded_tool_call_model_still_follows_main(ui, ui_page):
    """The composer SEEDS tool_call = main, and that seed must keep tracking main — otherwise
    this fix would freeze every conversation's tool role at its first model."""
    _two_models(ui)
    slug, conv = start_conversation(ui, ui_page, "answer a few questions")
    path = _set_roles(conv, main="m", tool_call="m")   # equal => seeded, not chosen
    ui_page.reload()

    _main_picker(ui_page).select_option("m2")
    models = _apply(ui_page, slug)
    assert models["main"] == "m2"
    assert models["tool_call"] == "m2"                 # follows main, as the composer does
    until(lambda: _stored(path).get("models", {}).get("main") == "m2",
          what="the saved chat role")


def test_clearing_main_keeps_a_chosen_tool_call_model(ui, ui_page):
    """Blank main means 'the system default' — it is not a reason to drop the tool role."""
    _two_models(ui)
    slug, conv = start_conversation(ui, ui_page, "keep the tool role")
    path = _set_roles(conv, main="m", tool_call="m2")
    ui_page.reload()

    _main_picker(ui_page).select_option("")
    models = _apply(ui_page, slug)
    assert "main" not in models                        # cleared to the system default
    assert models["tool_call"] == "m2"                 # the chosen role stays
    until(lambda: _stored(path).get("models", {}).get("tool_call") == "m2",
          what="the kept tool-call role")
