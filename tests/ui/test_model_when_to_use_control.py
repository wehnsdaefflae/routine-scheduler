"""The "when to use" note is EDITABLE on the model's own card, and what is typed persists.

D279(a). The field is the operator's only way in: `list_models` returns the note to a run
choosing a `model`, but nothing writes it except this control. A field that renders and silently
drops its value on save is worse than no field, because the advice reads as recorded — so what
is pinned here is the round trip through the real browser and the real save path, read back from
the config file the daemon loads.

The API-level half (the field's default, the serve shapes, the blank-drop rule, and that
nothing in the engine routes on it) is `tests/test_model_when_to_use.py`.
"""

from playwright.sync_api import expect

from rsched.config import load_server_config

HEADERS = {"Authorization": "Bearer ui-test-token"}
ADVICE = "cheap and blunt - fine for a long gate wait, never for a judgement"


def _card(ui_page, name):
    """The one model card for `name`, opened for editing — located by its own <strong>
    heading, since the enclosing models panel also carries the name as text."""
    card = ui_page.locator("div.panel", has=ui_page.locator("strong", has_text=name)).last
    card.get_by_text("edit fields", exact=True).click()
    return card


def _catalog(ui, ui_page):
    assert ui_page.request.post(f"{ui.url}/api/settings/endpoints", headers=HEADERS,
        data={"name": "note-ep", "kind": "openai"}).ok
    assert ui_page.request.post(f"{ui.url}/api/settings/models", headers=HEADERS,
        data={"name": "scribe", "endpoint": "note-ep", "model": "scribe-id"}).ok


def test_the_note_is_typed_on_the_model_card_and_survives_the_save(ui, ui_page):
    """The whole control, end to end: an empty field on a model that has no advice, the
    operator's sentence typed into it, and that sentence in the config the daemon reads."""
    _catalog(ui, ui_page)
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = _card(ui_page, "scribe")

    field = card.get_by_label("when to use (for a run choosing a model)", exact=True)
    expect(field).to_have_count(1)
    expect(field).to_have_value("")          # no advice recorded is the normal state
    # The placeholder is where an operator learns what the field is for, since the label
    # cannot say it in five words.
    assert "when to use this model" in (field.get_attribute("placeholder") or "")

    field.fill(ADVICE)
    card.get_by_role("button", name="save changes", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("scribe: updated")

    # Read back from the FILE the daemon loads, not from the page that just wrote it.
    cfg, problems = load_server_config(ui.server_cfg.source)
    assert cfg.models["scribe"].note == ADVICE, cfg.models["scribe"].note
    assert not [p for p in problems if "note" in p], problems

    # …and it is what the card shows on a reload, so the operator can see what is recorded.
    ui_page.reload()
    expect(_card(ui_page, "scribe").get_by_label(
        "when to use (for a run choosing a model)", exact=True)).to_have_value(ADVICE)


def test_clearing_the_note_removes_it_rather_than_storing_an_empty_line(ui, ui_page):
    """Blank is UNSET, not "no opinion recorded": an empty string would print a bare line in
    every catalog listing of every run that asks for models."""
    _catalog(ui, ui_page)
    assert ui_page.request.put(f"{ui.url}/api/settings/models/scribe", headers=HEADERS,
        data={"name": "scribe", "endpoint": "note-ep", "model": "scribe-id",
              "note": ADVICE}).ok
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = _card(ui_page, "scribe")

    card.get_by_label("when to use (for a run choosing a model)", exact=True).fill("")
    card.get_by_role("button", name="save changes", exact=True).click()
    expect(ui_page.locator("#toast")).to_contain_text("scribe: updated")

    assert load_server_config(ui.server_cfg.source)[0].models["scribe"].note == ""
