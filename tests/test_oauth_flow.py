"""OAuth connect flow over the real app: authorize-start builds a PKCE authorize URL, the public
/oauth/callback exchanges the code (httpx mocked) and stores the connection, and a bad/expired
`state` is rejected without storing anything."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from rsched import secrets
from rsched.oauth import store
from rsched.web.settings import oauth as oauth_mod


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def oauth_client(api_client, monkeypatch):
    client, tmp_path = api_client
    monkeypatch.setattr(store, "connections_path", lambda: tmp_path / "connections.json")
    client.app.state.server.public_url = "https://host.ts.net"
    secrets.set_secret("NOTION_OAUTH_CLIENT_ID", "cid-123")
    secrets.set_secret("NOTION_OAUTH_CLIENT_SECRET", "sek")
    # in-flight state is process-global; keep tests independent
    oauth_mod._flows.clear()
    oauth_mod._state_index.clear()
    return client, tmp_path


def _start(client, account="acme"):
    r = client.post("/api/settings/oauth/notion/authorize-start", json={"account": account})
    assert r.status_code == 200, r.text
    body = r.json()
    state = parse_qs(urlparse(body["authorize_url"]).query)["state"][0]
    return body["flow_id"], state


def test_status_lists_providers(oauth_client):
    client, _ = oauth_client
    body = client.get("/api/settings/oauth").json()
    assert body["public_url_set"] is True
    notion = next(p for p in body["providers"] if p["id"] == "notion")
    assert notion["configured"] is True          # creds set in the fixture
    assert notion["console_url"] == "https://www.notion.so/my-integrations"
    assert body["connections"] == []


def test_authorize_url_has_pkce_and_notion_params(oauth_client):
    client, _ = oauth_client
    body = client.post("/api/settings/oauth/notion/authorize-start",
                       json={"account": "acme"}).json()
    q = parse_qs(urlparse(body["authorize_url"]).query)
    assert q["client_id"] == ["cid-123"]
    assert q["redirect_uri"] == ["https://host.ts.net/oauth/callback"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"]
    assert q["owner"] == ["user"]                 # Notion-specific authorize_extra


def test_authorize_requires_public_url(oauth_client):
    client, _ = oauth_client
    client.app.state.server.public_url = ""
    r = client.post("/api/settings/oauth/notion/authorize-start", json={"account": "a"})
    assert r.status_code == 400 and "public_url" in r.text


def test_authorize_requires_creds(oauth_client):
    client, _ = oauth_client
    secrets.delete_secret("NOTION_OAUTH_CLIENT_ID")
    r = client.post("/api/settings/oauth/notion/authorize-start", json={"account": "a"})
    assert r.status_code == 400 and "NOTION_OAUTH_CLIENT_ID" in r.text


def test_callback_exchanges_and_stores(oauth_client, monkeypatch):
    client, _ = oauth_client
    flow_id, state = _start(client, "acme")
    monkeypatch.setattr(oauth_mod.httpx, "post",
                        lambda *a, **k: _Resp(200, {"access_token": "AT",
                                                    "workspace_name": "ACME Inc"}))
    page = client.get(f"/oauth/callback?state={state}&code=the-code")
    assert page.status_code == 200 and "Connected" in page.text
    conn = store.get_connection("notion", "acme")
    assert conn is not None and conn.access_token == "AT" and conn.label == "ACME Inc"
    assert conn.expires_at == 0.0 and conn.refresh_token == ""      # Notion: long-lived, no refresh
    # the flow now reads back as connected
    poll = client.get(f"/api/settings/oauth/flow/{flow_id}").json()
    assert poll["status"] == "connected"


def test_callback_bad_state_stores_nothing(oauth_client):
    client, _ = oauth_client
    _start(client, "acme")
    page = client.get("/oauth/callback?state=forged&code=x")
    assert page.status_code == 400 and "unknown or expired" in page.text
    assert store.get_connection("notion", "acme") is None


def test_callback_provider_error(oauth_client):
    client, _ = oauth_client
    _flow, state = _start(client, "acme")
    page = client.get(f"/oauth/callback?state={state}&error=access_denied")
    assert page.status_code == 400 and "access_denied" in page.text
    assert store.get_connection("notion", "acme") is None


def test_delete_connection(oauth_client, monkeypatch):
    client, _ = oauth_client
    _flow, state = _start(client, "acme")
    monkeypatch.setattr(oauth_mod.httpx, "post",
                        lambda *a, **k: _Resp(200, {"access_token": "AT"}))
    client.get(f"/oauth/callback?state={state}&code=c")
    assert store.get_connection("notion", "acme") is not None
    r = client.delete("/api/settings/oauth/notion/acme")
    assert r.status_code == 200
    assert store.get_connection("notion", "acme") is None
    assert client.delete("/api/settings/oauth/notion/acme").status_code == 404


def test_needed_secrets_excludes_connection_tokens(oauth_client):
    client, tmp_path = oauth_client
    util = tmp_path / "library" / "utils" / "connu" / "main.py"
    util.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(
        "# /// script\n# dependencies = []\n# ///\n"
        '"""connu — util needing tokens.\n\n'
        "usage: gu connu\ncalls: (none)\n"
        "secrets: NOTION_ACCESS_TOKEN, NOTION_TOKEN, FTP_SOURCES\n"
        "tags: test\nnet: outbound\n"
        '"""\n', encoding="utf-8")
    entries = {n["key"]: n for n in client.get("/api/settings/secrets").json()["needed"]}
    assert "NOTION_ACCESS_TOKEN" not in entries       # engine-injected from a connection, not user-set
    assert "NOTION_TOKEN" in entries                  # the static-token alternative IS user-set
    assert "FTP_SOURCES" in entries
    # the declaring util's usage + doc ride along so the UI can show the secret's format
    assert entries["FTP_SOURCES"]["doc"]
    assert "connu" in entries["FTP_SOURCES"]["usage"]


def test_set_public_url_validates(oauth_client):
    client, _ = oauth_client
    assert client.put("/api/settings/oauth/public-url",
                      json={"public_url": "ftp://nope"}).status_code == 400
    r = client.put("/api/settings/oauth/public-url", json={"public_url": "https://h.ts.net/"})
    assert r.status_code == 200
    assert r.json()["public_url"] == "https://h.ts.net"           # trailing slash trimmed
    assert client.app.state.server.public_url == "https://h.ts.net"
