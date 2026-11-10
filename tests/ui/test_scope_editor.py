"""The scope editor on Settings → Connections (2026-11-09), through the real page.

What it is for, asserted here: the requested scopes are SHOWN grouped by API with a count
against the consent ceiling; a scope the provider's token cannot use is said; each connected
account's GRANTED scopes are compared with the list as edited; and a save writes the very
Secrets entry the next consent reads.
"""

from playwright.sync_api import expect

from rsched import secrets
from rsched.oauth import providers, store
from rsched.oauth.store import Connection

G = "https://www.googleapis.com/auth/"


def _seed(ui, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "cid-ui")
    secrets.set_secret("GOOGLE_OAUTH_SCOPES",
                       f"openid {G}gmail.modify {G}googlehealth.sleep.readonly")
    store.set_connection(Connection(provider="google", account="personal", access_token="t",
                                    refresh_token="r", expires_at=0.0, obtained_at=0.0,
                                    scopes=["openid", G + "gmail.modify"], label="personal"))


def test_the_editor_shows_edits_and_saves_the_consent_scopes(ui, ui_page, monkeypatch):
    _seed(ui, monkeypatch)
    ui_page.goto(f"{ui.url}/#/settings?section=connections")
    ui_page.locator('[data-scopes-of="google"] summary').click()
    editor = ui_page.locator('[data-scope-editor="google"]')
    expect(editor.locator("[data-scope-count]")).to_have_text("3 / 50 scopes")
    expect(editor).to_contain_text("refused by the Health API")
    expect(editor.locator('[data-scope-group="gmail"]')).to_contain_text("gmail.modify")
    account = editor.locator('[data-scope-account="personal"]')
    expect(account).to_contain_text("1 not granted yet")

    editor.locator(f'[data-scope-remove="{G}googlehealth.sleep.readonly"]').click()
    expect(account).to_contain_text("granted exactly this list")
    editor.locator("input").fill("drive")
    editor.get_by_role("button", name="add", exact=True).click()
    expect(editor.locator("[data-scope-count]")).to_have_text("3 / 50 scopes")
    expect(account).to_contain_text("1 not granted yet")

    editor.locator("[data-scope-save]").click()
    expect(ui_page.locator("#toast")).to_contain_text("reconnect to apply")
    assert providers.authorize_scopes("google") == f"openid {G}gmail.modify {G}drive"


def test_the_health_connection_says_what_it_may_carry(ui, ui_page, monkeypatch):
    _seed(ui, monkeypatch)
    ui_page.goto(f"{ui.url}/#/settings?section=connections")
    expect(ui_page.locator('[data-provider="google_health"]')).to_contain_text("Google Health")
    ui_page.locator('[data-scopes-of="google_health"] summary').click()
    editor = ui_page.locator('[data-scope-editor="google_health"]')
    expect(editor).to_contain_text("only googlehealth.")
    expect(editor).to_contain_text("uses the google OAuth app")
