"""A provider's consent scopes as an edited LIST, and Google Health as its own connection.

Two defects drove it (2026-11-09). Editing `GOOGLE_OAUTH_SCOPES` meant retyping a write-only
48-entry secret before a reconnect on another card, so llmsectest-weekday's Drive and Docs calls
403'd for want of two scopes nobody could see were missing. And the Google Health API refuses a
token carrying ANY scope outside its own, so deficit-coach's Health calls could never succeed on
the one shared Google token whatever was added to it.
"""

from __future__ import annotations

import pytest

from rsched import secrets
from rsched.oauth import providers, scopes, store

G = "https://www.googleapis.com/auth/"


def test_google_health_is_the_google_app_with_its_own_scopes_and_token():
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "cid-g")
    creds = providers.client_creds("google_health")
    assert creds is not None and creds.client_id == "cid-g"
    assert providers.creds_secret_keys("google_health") == (
        "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET")
    assert providers.scopes_secret_key("google_health") == "GOOGLE_HEALTH_OAUTH_SCOPES"
    assert providers.access_token_var("google_health") == "GOOGLE_HEALTH_ACCESS_TOKEN"
    assert "GOOGLE_HEALTH_ACCESS_TOKEN" in providers.connection_token_vars()


def test_short_names_expand_and_scopes_group_by_their_api():
    assert scopes.normalize("google", "drive.readonly") == G + "drive.readonly"
    assert scopes.normalize("google", "openid") == "openid"
    assert scopes.normalize("google", G + "gmail.modify") == G + "gmail.modify"
    assert scopes.api_of(G + "gmail.modify") == "gmail"
    assert scopes.api_of(G + "googlehealth.sleep.readonly") == "googlehealth"
    assert scopes.api_of("openid") == "openid"
    assert scopes.split("a b,c\na") == ["a", "b", "c"]


def test_the_rules_refuse_what_cannot_work_and_warn_about_what_will_not():
    errors, _ = scopes.judge("google_health", [G + "gmail.readonly"])
    assert errors and "may only carry" in errors[0]
    errors, _ = scopes.judge("google", [G + f"s{i}" for i in range(51)])
    assert errors and "hangs past 50" in errors[0]
    errors, warnings = scopes.judge("google", [G + "googlehealth.sleep.readonly", "openid"])
    assert not errors and "refused by the Health API" in warnings[0]
    errors, _ = scopes.judge("notion", ["x"])
    assert errors


@pytest.fixture
def client(api_client, monkeypatch):
    c, tmp_path = api_client
    monkeypatch.setattr(store, "connections_path", lambda: tmp_path / "connections.json")
    return c


def test_the_editor_saves_the_list_the_consent_will_ask_for(client):
    r = client.put("/api/settings/oauth/google/scopes",
                   json={"scopes": ["openid", "drive", "documents", "drive"]})
    assert r.status_code == 200, r.text
    assert providers.authorize_scopes("google") == f"openid {G}drive {G}documents"
    google = next(p for p in client.get("/api/settings/oauth").json()["providers"]
                  if p["id"] == "google")
    assert google["requested"] == ["openid", G + "drive", G + "documents"]
    assert google["groups"][G + "drive"] == "drive" and google["max_scopes"] == 50


def test_the_editor_refuses_a_list_the_provider_cannot_carry(client):
    r = client.put("/api/settings/oauth/google_health/scopes", json={"scopes": ["gmail.modify"]})
    assert r.status_code == 400 and "may only carry" in r.json()["detail"]
    r = client.put("/api/settings/oauth/google/scopes",
                   json={"scopes": [f"s{i}" for i in range(51)]})
    assert r.status_code == 400
    assert providers.authorize_scopes("google_health") == ""     # nothing was written


GAPI = (
    "# /// script\n# dependencies = []\n# ///\n"
    '"""gapi2 — serves two connections.\n\n'
    "usage: gu gapi2 [--json]\n"
    "calls: (none)\n"
    "secrets: GOOGLE_ACCESS_TOKEN, GOOGLE_HEALTH_ACCESS_TOKEN\n"
    "tags: demo, testing\n"
    "net: none\nfs: roots\n"
    '"""\nimport sys\nsys.exit(1)\n'
)


def test_a_util_with_one_of_its_connections_bound_is_not_sent_to_ask_for_the_other(tmp_path):
    """google-api serves Google and Google Health. A routine binding Google whose Drive call
    failed had a token; routing it to request a Health connection would be wrong advice."""
    from types import SimpleNamespace

    from rsched import utils_lib
    from rsched.engine.exec_env import _unbound_connection_request
    from test_utils import _ctx

    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "gapi2", GAPI)
    ctx = _ctx(tmp_path)
    ctx.routine = SimpleNamespace(slug="demo", dir=tmp_path, fs_read_roots=[], fs_write_roots=[],
                                  connections={"google": "personal"}, machines=[])
    assert _unbound_connection_request(ctx, "gapi2") == ""
    assert "connection:google" in _unbound_connection_request(_ctx(tmp_path), "gapi2")


def test_the_cloud_console_is_linked_for_the_project_the_client_belongs_to(client):
    """The scope alone is not enough: Docs answered SERVICE_DISABLED with its scope granted. The
    editor links the project's console, read off the client id."""
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "746057774400-abc.apps.googleusercontent.com")
    client.put("/api/settings/oauth/google/scopes", json={"scopes": ["openid", "documents"]})
    provs = {p["id"]: p for p in client.get("/api/settings/oauth").json()["providers"]}
    console = provs["google"]["console"]
    assert console["project"] == "746057774400"
    assert console["enabled_apis"].endswith("/apis/dashboard?project=746057774400")
    assert set(console["api"]) == {"documents"}          # sign-in scopes enable no API
    assert "q=documents" in console["api"]["documents"]
    assert provs["google"]["console_url"].endswith("?project=746057774400")
    assert provs["google_health"]["console_url"].endswith("?project=746057774400")


def test_no_project_means_no_console_links(client):
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "not-a-google-client")
    client.put("/api/settings/oauth/google/scopes", json={"scopes": ["drive"]})
    google = next(p for p in client.get("/api/settings/oauth").json()["providers"]
                  if p["id"] == "google")
    assert google["console"] == {}
