"""A failed endpoint test explains itself: the card shows what the proxy's error MEANS above the
raw detail (a model its proxy lists but its upstream has not enabled for the account)."""

from playwright.sync_api import expect

from rsched.endpoints import failure_hints, model_ids
from rsched.endpoints.base import EndpointError
from rsched.endpoints.instrument import InstrumentedEndpoint

HEADERS = {"Authorization": "Bearer ui-test-token"}
RAW = 'proxy: HTTP 404: {"error":{"message":"Requested entity was not found."}}'


def test_the_card_shows_the_hint_above_the_raw_error(ui, ui_page, monkeypatch):
    monkeypatch.setattr(model_ids, "list_ids", lambda ep: {"ok": True, "ids": ["gem-4"]})

    def refuse(self, *a, **k):
        raise EndpointError(RAW)

    monkeypatch.setattr(InstrumentedEndpoint, "complete", refuse)
    assert ui_page.request.post(f"{ui.url}/api/settings/endpoints", headers=HEADERS,
        data={"name": "hint-ep", "kind": "openai", "base_url": "http://127.0.0.1:1/v1"}).ok
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")
    card = ui_page.locator("div.panel", has=ui_page.locator("strong", has_text="hint-ep")).first
    card.get_by_label("model to test", exact=True).select_option("gem-4")
    card.get_by_role("button", name="test", exact=True).click()
    hint = card.locator("[data-test-hint]")
    expect(hint).to_contain_text("not enabled for this account")
    assert failure_hints.explain(RAW) in hint.inner_text().replace("\n", " ")
    expect(card.locator(".test-result")).to_contain_text("Requested entity was not found")
