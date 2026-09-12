"""A model's fallbacks are PICKED from the catalog, so an impossible name cannot be entered.

The old field was free text: "Opus 5" was typeable, looked accepted, and was refused only by the
server after a round trip (three refused saves in the 2026-09-10 UI trace). These tests pin the
two halves of the repair — the catalog is offered, and the picked order is what persists.
"""

from playwright.sync_api import expect

from rsched.config import load_server_config

HEADERS = {"Authorization": "Bearer ui-test-token"}


def _card(ui_page, name):
    """The one model card for `name` — located by its own <strong> heading, since the
    enclosing models panel also contains the name as text."""
    card = ui_page.locator("div.panel", has=ui_page.locator("strong", has_text=name)).last
    card.get_by_text("edit fields", exact=True).click()
    return card


def _catalog(ui, ui_page):
    assert ui_page.request.post(f"{ui.url}/api/settings/endpoints", headers=HEADERS,
        data={"name": "fb-ep", "kind": "openai"}).ok
    for name in ("primary", "backup", "spare"):
        assert ui_page.request.post(f"{ui.url}/api/settings/models", headers=HEADERS,
            data={"name": name, "endpoint": "fb-ep", "model": f"{name}-id"}).ok


def test_fallbacks_are_picked_from_the_catalog_and_persist_in_order(ui, ui_page):
    _catalog(ui, ui_page)
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = _card(ui_page, "primary")

    # The control is a SELECT over the catalog — not a text box. Only the OTHER models are
    # offered: a model may never fall back to itself (the server refuses that too).
    picker = card.get_by_label("add a fallback model", exact=True)
    expect(picker).to_have_count(1)
    options = picker.locator("option").all_inner_texts()
    assert "backup" in options and "spare" in options, options
    assert "primary" not in options, "a model must not offer itself as its own fallback"

    picker.select_option("spare")
    card.get_by_label("add a fallback model", exact=True).select_option("backup")
    card.get_by_role("button", name="save changes", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("primary: updated")

    # Picked order IS the failover order, and it survives a reload.
    cfg, problems = load_server_config(ui.server_cfg.source)
    assert cfg.models["primary"].fallbacks == ["spare", "backup"], cfg.models["primary"].fallbacks
    assert not [p for p in problems if "fallback" in p], problems
    ui_page.reload()
    expect(ui_page.get_by_title("failover step").first).to_contain_text("1. spare")


def test_a_picked_fallback_can_be_removed_again(ui, ui_page):
    _catalog(ui, ui_page)
    assert ui_page.request.put(f"{ui.url}/api/settings/models/primary", headers=HEADERS,
        data={"name": "primary", "endpoint": "fb-ep", "model": "primary-id",
              "fallbacks": ["backup"]}).ok
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = _card(ui_page, "primary")
    card.get_by_title("remove backup", exact=True).click()
    card.get_by_role("button", name="save changes", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("primary: updated")
    assert load_server_config(ui.server_cfg.source)[0].models["primary"].fallbacks == []
