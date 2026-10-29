"""Settings → Connections card: the OAuth section renders providers, gates the connect button on
BOTH a configured app (creds in Secrets) and a saved redirect URL, and saving the redirect URL
persists to config.yaml and re-enables connect. (No real OAuth round-trip — that needs a provider.)"""

import re

import yaml
from playwright.sync_api import expect

from rsched import secrets
from rsched.oauth import store
from rsched.oauth.store import Connection

from .conftest import until
from .helpers import unfold


def test_connections_card(ui, ui_page, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    secrets.set_secret("NOTION_OAUTH_CLIENT_ID", "cid-ui")   # → Notion shows "app configured"

    ui_page.goto(f"{ui.url}/#/settings?section=connections")
    ui_page.wait_for_selector("#sec-connections")

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
    """The routine page binds a connected account in its settings draft; the one accept writes
    routine.yaml `connections:`."""
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))

    ui_page.goto(f"{ui.url}/#/routine/uir")
    unfold(ui_page)
    row = ui_page.locator('[data-conn-row="notion"]')
    row.wait_for()
    row.locator("select").select_option("acme")
    ui_page.locator(".accept-bar [data-accept]").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("accepted")

    path = ui.routine_dir("uir") / "routine.yaml"
    until(lambda: yaml.safe_load(path.read_text(encoding="utf-8")).get("connections")
          == {"notion": "acme"}, what="the accepted connection")


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

    # the panel's OWN disclosure: `> summary`, because each abilities card inside it now
    # folds its met requirements behind a "N requirements · all met" summary of its own
    # (components/abilities.js), so a descendant match is no longer unique.
    ui_page.locator(".conv-caps > summary").click()   # ⚙ capabilities & budgets
    row = ui_page.locator('[data-conn-row="notion"]')
    row.wait_for()
    row.locator("select").select_option("acme")
    ui_page.get_by_role("button", name="save connections").click()
    expect(ui_page.locator("#toast:not([hidden])")).to_contain_text("connections saved")

    raw = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["connections"] == {"notion": "acme"}



def test_a_double_clicked_connections_save_patches_once(ui, ui_page):
    """The card's save hand-rolled the try/toast shape without disabling the button, so a
    double-click PATCHed the binding twice. It is util.js `act()` now: disabled while out."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("bind my accounts")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    ui_page.locator(".conv-caps > summary").click()   # ⚙ capabilities & budgets
    save = ui_page.get_by_role("button", name="save connections")
    expect(save).to_be_visible()

    patches = []

    def hold(route):
        if route.request.method == "PATCH":
            patches.append(route)
        else:
            route.fallback()

    detail = re.compile(rf"/api/conversations/{slug}$")
    ui_page.route(detail, hold)
    save.dblclick()
    until(lambda: patches, what="the connections PATCH", page=ui_page)
    ui_page.wait_for_timeout(400)
    assert len(patches) == 1, f"{len(patches)} PATCHes for one save"
    expect(save).to_be_disabled()
    ui_page.unroute(detail)                            # releases the held PATCH
    expect(save).to_be_enabled()


def test_reconnect_re_consents_in_place_and_never_disconnects(ui, ui_page, monkeypatch):
    """Re-consent (a widened GOOGLE_OAUTH_SCOPES, a flagged needs_reauth) used to mean
    disconnect-then-connect, so a connect that failed — a broken redirect, 2026-10-29 — left the
    account with no token at all. Reconnect starts the SAME authorize flow for the row's own
    provider:account and sends no DELETE: the callback overwrites that one key only on success."""
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    secrets.set_secret("NOTION_OAUTH_CLIENT_ID", "cid-ui")
    store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))
    store.set_connection(Connection(provider="notion", account="stale", access_token="OLD",
                                    needs_reauth=True))

    ui_page.goto(f"{ui.url}/#/settings?section=connections")
    ui_page.wait_for_selector("#sec-connections")
    url_row = ui_page.locator("[data-conn-url]")
    url_row.locator("input").fill("https://host.ts.net")
    url_row.get_by_role("button", name="save").click()
    expect(ui_page.locator("[data-conn-callback]")).to_have_text("https://host.ts.net/oauth/callback")

    # a flagged row says what to do and makes reconnect its primary control
    stale = ui_page.locator('[data-conn="notion:stale"]')
    expect(stale).to_contain_text("needs re-auth — reconnect")
    expect(stale.locator("[data-conn-reconnect]")).to_have_class(re.compile(r"\bprimary\b"))

    calls = []

    def oauth(route):
        req = route.request
        calls.append((req.method, req.url, req.post_data_json if req.method == "POST" else None))
        if req.url.endswith("/authorize-start"):
            route.fulfill(json={"authorize_url": "about:blank", "flow_id": "f-ui"})
        elif "/oauth/flow/" in req.url:
            route.fulfill(json={"status": "pending"})
        else:
            route.fallback()

    ui_page.route(re.compile(r"/api/settings/oauth/"), oauth)
    ui_page.evaluate("window.open = () => null")     # no consent tab in the shared browser
    ui_page.locator('[data-conn="notion:acme"] [data-conn-reconnect]').click()
    until(lambda: any(u.endswith("/authorize-start") for _, u, _ in calls),
          what="the authorize-start POST", page=ui_page)
    ui_page.wait_for_timeout(300)
    ui_page.unroute(re.compile(r"/api/settings/oauth/"))

    starts = [(m, u, b) for m, u, b in calls if u.endswith("/authorize-start")]
    assert starts == [("POST", f"{ui.url}/api/settings/oauth/notion/authorize-start",
                       {"account": "acme"})]
    assert not [c for c in calls if c[0] == "DELETE"], calls
    assert store.get_connection("notion", "acme").access_token == "AT"
