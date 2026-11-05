"""The connect/reconnect button does not hand the operator to the provider's opaque refusal.

R3061: re-authorizing a Google connection on this instance showed Google's own error page
("Access blocked: Authorization error — device_id and device_name are required for private IP",
naming our app and 192.168.0.128) instead of a consent screen, because the instance's public_url
is a LAN address and Google refuses a private-IP redirect_uri before consent. authorize-start now
returns a `caveat`; the card states it and asks, rather than opening a doomed tab silently.

A WARNING, not a block: confirming still opens the provider's tab (the operator may be on a
tunnel this check cannot see). Cancelling opens nothing.
"""

import re

import pytest
from playwright.sync_api import expect

from rsched import secrets
from rsched.oauth import store

from .conftest import until


def _route_oauth(ui_page, caveat: str):
    """Serve authorize-start with `caveat` and record every window.open the card attempts.

    The record lives IN THE PAGE (read back with `_opened`): the consent tab must never open in
    the shared browser, and a page-side array is what the sibling suite's `window.open = () =>
    null` stub already does, one step further.
    """
    def oauth(route):
        req = route.request
        if req.url.endswith("/authorize-start"):
            route.fulfill(json={"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                                "flow_id": "f-cav", "caveat": caveat})
        elif "/oauth/flow/" in req.url:
            route.fulfill(json={"status": "pending"})
        else:
            route.fallback()

    ui_page.route(re.compile(r"/api/settings/oauth/"), oauth)
    # A FUNCTION EXPRESSION, not a bare statement list: playwright calls an evaluated string
    # whose completion value is a function, so a script ENDING in `window.open = (u) => …`
    # got called straight back with no argument — recording a phantom open(undefined) before
    # the card had done anything at all.
    ui_page.evaluate("() => { window.__opened = []; "
                     "window.open = (u) => { window.__opened.push(u); return null; }; }")


def _opened(ui_page) -> list:
    return ui_page.evaluate("window.__opened")


def _open_card(ui, ui_page, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: ui.tmp / "connections.json")
    secrets.set_secret("NOTION_OAUTH_CLIENT_ID", "cid-cav")
    ui_page.goto(f"{ui.url}/#/settings?section=connections")
    ui_page.wait_for_selector("#sec-connections")
    url_row = ui_page.locator("[data-conn-url]")
    url_row.locator("input").fill("http://192.168.0.128:8820")
    url_row.get_by_role("button", name="save").click()
    expect(ui_page.locator("[data-conn-callback]")).to_have_text(
        "http://192.168.0.128:8820/oauth/callback")
    notion = ui_page.locator('[data-provider="notion"]')
    notion.locator("input").fill("acme")
    return notion


CAVEAT = ("The redirect target 192.168.0.128 is a private address (LAN, loopback or "
          "link-local). Google refuses an authorization request whose redirect_uri is a "
          "private IP before it shows any consent screen.")


@pytest.mark.ui
def test_a_caveat_is_stated_before_the_consent_tab_opens(ui, ui_page, monkeypatch):
    notion = _open_card(ui, ui_page, monkeypatch)
    _route_oauth(ui_page, CAVEAT)

    notion.get_by_role("button", name="connect").click()
    dialog = ui_page.locator(".modal-overlay .panel")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".dlg-msg")).to_contain_text("192.168.0.128")
    expect(dialog.locator(".dlg-msg")).to_contain_text("private address")
    assert _opened(ui_page) == [], "the consent tab opened before he was told why it will fail"

    # the override: he may know better than this inference
    dialog.get_by_role("button", name="authorize anyway").click()
    expect(dialog).not_to_be_attached()
    until(lambda: _opened(ui_page), what="the consent tab to open after the override",
          page=ui_page)
    assert _opened(ui_page) == ["https://accounts.google.com/o/oauth2/v2/auth"]
    ui_page.unroute(re.compile(r"/api/settings/oauth/"))


@pytest.mark.ui
def test_cancelling_the_caveat_opens_no_tab(ui, ui_page, monkeypatch):
    notion = _open_card(ui, ui_page, monkeypatch)
    _route_oauth(ui_page, CAVEAT)

    notion.get_by_role("button", name="connect").click()
    dialog = ui_page.locator(".modal-overlay .panel")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="cancel").click()
    expect(dialog).not_to_be_attached()
    assert _opened(ui_page) == []
    ui_page.unroute(re.compile(r"/api/settings/oauth/"))


@pytest.mark.ui
def test_no_caveat_opens_the_tab_straight_away(ui, ui_page, monkeypatch):
    """The working flow must not grow a click: no caveat, no dialog."""
    notion = _open_card(ui, ui_page, monkeypatch)
    _route_oauth(ui_page, "")

    notion.get_by_role("button", name="connect").click()
    until(lambda: _opened(ui_page), what="the consent tab to open with no dialog", page=ui_page)
    expect(ui_page.locator(".modal-overlay")).not_to_be_attached()
    assert _opened(ui_page) == ["https://accounts.google.com/o/oauth2/v2/auth"]
    ui_page.unroute(re.compile(r"/api/settings/oauth/"))
