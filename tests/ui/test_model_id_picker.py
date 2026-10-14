"""The model-id field is a dropdown of what the endpoint itself serves — a plain text box only
when it cannot say, with the reason beside it — and a model has no effort control of its own."""

from playwright.sync_api import expect

from rsched.config import load_server_config
from rsched.endpoints import model_ids

HEADERS = {"Authorization": "Bearer ui-test-token"}


def _catalog(ui, ui_page, monkeypatch, listing):
    """One openai endpoint with one catalog model; `listing` is what the endpoint says."""
    monkeypatch.setattr(model_ids, "list_ids", lambda ep: listing)
    assert ui_page.request.post(f"{ui.url}/api/settings/endpoints", headers=HEADERS,
        data={"name": "pick-ep", "kind": "openai", "base_url": "http://127.0.0.1:1/v1"}).ok
    assert ui_page.request.post(f"{ui.url}/api/settings/models", headers=HEADERS,
        data={"name": "held", "endpoint": "pick-ep", "model": "held-id"}).ok


def test_the_add_model_form_offers_the_endpoints_own_ids_and_saves_the_pick(
        ui, ui_page, monkeypatch):
    _catalog(ui, ui_page, monkeypatch, {"ok": True, "ids": ["alpha-1", "beta-2"]})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    addm = ui_page.locator("details.panel", has_text="+ add model")
    addm.locator("summary").click()
    picker = addm.get_by_label("model id", exact=True)
    expect(picker).to_have_js_property("tagName", "SELECT")
    expect(picker.locator("option")).to_have_text(["— choose a model id —", "alpha-1", "beta-2"])
    expect(addm).to_contain_text("2 offered by this endpoint")
    addm.get_by_placeholder("name (e.g. gpt-4o)").fill("beta")
    picker.select_option("beta-2")
    addm.get_by_role("button", name="add model", exact=True).click()
    expect(ui_page.locator("strong", has_text="beta").first).to_be_visible()
    assert load_server_config(ui.server_cfg.source)[0].models["beta"].model == "beta-2"


def test_a_saved_value_the_listing_lacks_stays_selectable_and_is_marked(
        ui, ui_page, monkeypatch):
    _catalog(ui, ui_page, monkeypatch, {"ok": True, "ids": ["alpha-1"]})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator("div.panel", has=ui_page.locator("strong", has_text="held")).last
    card.get_by_text("edit fields", exact=True).click()
    picker = card.get_by_label("model id", exact=True)
    expect(picker).to_have_js_property("tagName", "SELECT")
    expect(picker).to_have_value("held-id")
    expect(picker.locator("option")).to_have_text(["held-id (not listed)", "alpha-1"])
    card.get_by_role("button", name="save changes", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("held: updated")
    assert load_server_config(ui.server_cfg.source)[0].models["held"].model == "held-id"


def test_a_failed_listing_falls_back_to_a_text_box_and_says_why(ui, ui_page, monkeypatch):
    _catalog(ui, ui_page, monkeypatch,
             {"ok": False, "error": "The endpoint's model listing answered HTTP 401; check its key."})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    addm = ui_page.locator("details.panel", has_text="+ add model")
    addm.locator("summary").click()
    box = addm.get_by_label("model id", exact=True)
    expect(box).to_have_js_property("tagName", "INPUT")
    expect(addm).to_contain_text("answered HTTP 401")
    expect(addm).to_contain_text("type the id")
    addm.get_by_placeholder("name (e.g. gpt-4o)").fill("typed")
    box.fill("by-hand-id")
    addm.get_by_role("button", name="add model", exact=True).click()
    expect(ui_page.locator("strong", has_text="typed").first).to_be_visible()
    assert load_server_config(ui.server_cfg.source)[0].models["typed"].model == "by-hand-id"


def test_an_endpoint_that_lists_nothing_keeps_the_text_box(ui, ui_page, monkeypatch):
    _catalog(ui, ui_page, monkeypatch, {"ok": True, "ids": []})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    addm = ui_page.locator("details.panel", has_text="+ add model")
    addm.locator("summary").click()
    expect(addm.get_by_label("model id", exact=True)).to_have_js_property("tagName", "INPUT")
    expect(addm).to_contain_text("lists no models")


def test_the_endpoint_cards_test_box_offers_the_listed_ids(ui, ui_page, monkeypatch):
    _catalog(ui, ui_page, monkeypatch, {"ok": True, "ids": ["alpha-1", "beta-2"]})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator("div.panel", has=ui_page.locator("strong", has_text="pick-ep")).first
    test_box = card.get_by_label("model to test", exact=True)
    expect(test_box).to_have_js_property("tagName", "SELECT")
    expect(test_box.locator("option")).to_have_text(["— choose a model id —", "alpha-1", "beta-2"])
    card.get_by_role("button", name="test", exact=True).click()       # nothing chosen yet
    expect(ui_page.locator("#toast")).to_contain_text("enter a model id to test")


def test_a_catalog_model_has_no_effort_control(ui, ui_page, monkeypatch):
    """Effort is chosen per role (the routine page, a conversation) and for the two
    instance-wide models — never on the catalog entry."""
    _catalog(ui, ui_page, monkeypatch, {"ok": True, "ids": ["held-id"]})
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator("div.panel", has=ui_page.locator("strong", has_text="held")).last
    card.get_by_text("edit fields", exact=True).click()
    expect(card.locator("label.field", has_text="Context window")).to_be_visible()
    # the field LABELS (the temperature hint rightly mentions the word "effort")
    names = [n.lower() for n in card.locator("label.field > span:not(.hint)").all_inner_texts()]
    assert "model id" in names and "effort" not in names, names
    addm = ui_page.locator("details.panel", has_text="+ add model")
    addm.locator("summary").click()
    add_names = [n.lower() for n in addm.locator("label.field > span:not(.hint)").all_inner_texts()]
    assert "model id" in add_names and "effort" not in add_names, add_names
