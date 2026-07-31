"""Settings → Connections card: the OAuth section renders providers, gates the connect button on
BOTH a configured app (creds in Secrets) and a saved redirect URL, and saving the redirect URL
persists to config.yaml and re-enables connect. (No real OAuth round-trip — that needs a provider.)"""

import yaml
from playwright.sync_api import expect

from rsched import secrets
from rsched.oauth import store
from rsched.oauth.store import Connection


def test_connections_card(ui, ui_page, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    secrets.set_secret("NOTION_OAUTH_CLIENT_ID", "cid-ui")   # → Notion shows "app configured"

    ui_page.goto(f"{ui.url}/#/settings?section=connections")
    ui_page.wait_for_selector("#sec-connections", timeout=10_000)

    notion = ui_page.locator('[data-provider="notion"]')
    expect(notion).to_contain_text("Notion")
    expect(notion).to_contain_text("app configured")
    # a straight link to where you create the OAuth app
    expect(ui_page.locator('[data-provider-link="notion"]')).to_have_attribute(
        "href", "https://www.notion.so/my-integrations")
    expect(ui_page.locator("[data-conn-empty]")).to_contain_text("none yet")
    # connect is gated on the redirect URL (not set yet)
    expect(notion.get_by_role("button", name="connect")).to_be_disabled()

    # save the redirect URL → the card re-renders, keeps the value, and connect enables
    url_row = ui_page.locator("[data-conn-url]")
    url_row.locator("input").fill("https://host.ts.net")
    url_row.get_by_role("button", name="save").click()
    expect(ui_page.locator("[data-conn-url] input")).to_have_value("https://host.ts.net")
    # the exact callback to register is derived + shown (no guessing the /oauth/callback path)
    expect(ui_page.locator("[data-conn-callback]")).to_have_text("https://host.ts.net/oauth/callback")
    expect(ui_page.locator('[data-provider="notion"]').get_by_role(
        "button", name="connect")).to_be_enabled()

    # persisted to config.yaml (not just the live object)
    raw = yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))
    assert raw["public_url"] == "https://host.ts.net"


def test_routine_connection_binding(ui, ui_page, monkeypatch):
    """The routine page binds a connected account; the PATCH writes routine.yaml `connections:`."""
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))

    ui_page.goto(f"{ui.url}/#/routine/uir")
    row = ui_page.locator('[data-conn-row="notion"]')
    row.wait_for(timeout=10_000)
    row.locator("select").select_option("acme")
    ui_page.get_by_role("button", name="save connections").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("connections saved")

    raw = yaml.safe_load((ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["connections"] == {"notion": "acme"}


def test_conversation_connection_binding(ui, ui_page, monkeypatch):
    """D55 (closes R70): a CONVERSATION binds a connected account with the SAME shared card as a
    routine; the PATCH writes the conversation's routine.yaml `connections:` (where the engine's
    token injection reads it). Before this the Connections surface existed only on routine pages,
    so a conversation could not use connector utils (google-api)."""
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))

    # create a conversation, then open its capabilities panel where the card lives
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("read my notion pages")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]

    ui_page.locator(".conv-caps summary").click()   # ⚙ capabilities & budgets
    row = ui_page.locator('[data-conn-row="notion"]')
    row.wait_for(timeout=10_000)
    row.locator("select").select_option("acme")
    ui_page.get_by_role("button", name="save connections").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("connections saved")

    raw = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["connections"] == {"notion": "acme"}

