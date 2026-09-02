"""OAuth connect flow over the real app: authorize-start builds a PKCE authorize URL, the public
/oauth/callback exchanges the code (httpx mocked) and stores the connection, and a bad/expired
`state` is rejected without storing anything."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from rsched import secrets
from rsched.oauth import exchange, store
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
    monkeypatch.setattr(exchange.httpx, "post",
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
    monkeypatch.setattr(exchange.httpx, "post",
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
        "tags: test\nnet: outbound\nfs: roots\n"
        '"""\n', encoding="utf-8")
    entries = {n["key"]: n for n in client.get("/api/settings/secrets").json()["needed"]}
    assert "NOTION_ACCESS_TOKEN" not in entries       # engine-injected from a connection, not user-set
    assert "NOTION_TOKEN" in entries                  # the static-token alternative IS user-set
    assert "FTP_SOURCES" in entries
    # the declaring util's usage + doc ride along so the UI can show the secret's format
    assert entries["FTP_SOURCES"]["doc"]
    assert "connu" in entries["FTP_SOURCES"]["usage"]


def test_needed_secret_set_from_environment(oauth_client, monkeypatch):
    """F209: a declared secret provisioned via the daemon ENVIRONMENT (os.environ) — not the
    store file — reads as SET, because utils_run._child_env injects it into a declaring util.
    Presence is the union of the store and os.environ; the store stays the only writable side.
    """
    client, tmp_path = oauth_client
    util = tmp_path / "library" / "utils" / "envu" / "main.py"
    util.parent.mkdir(parents=True, exist_ok=True)
    util.write_text(
        "# /// script\n# dependencies = []\n# ///\n"
        '"""envu — util needing an env secret.\n\n'
        "usage: gu envu\ncalls: (none)\n"
        "secrets: WEBAUTHSOURCES\ntags: test\nnet: outbound\nfs: roots\n"
        '"""\n', encoding="utf-8")
    # not in the store → reads unset
    entries = {n["key"]: n for n in client.get("/api/settings/secrets").json()["needed"]}
    assert entries["WEBAUTHSOURCES"]["set"] is False
    # provisioned via the environment → now reads set (the Webauthsources symptom, fixed)
    monkeypatch.setenv("WEBAUTHSOURCES", "provisioned-in-env")
    entries2 = {n["key"]: n for n in client.get("/api/settings/secrets").json()["needed"]}
    assert entries2["WEBAUTHSOURCES"]["set"] is True
    # never the value
    assert "provisioned-in-env" not in client.get("/api/settings/secrets").text


def _set_google(*, with_scopes: bool = True) -> None:
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_ID", "gid")
    secrets.set_secret("GOOGLE_OAUTH_CLIENT_SECRET", "gsec")
    if with_scopes:
        secrets.set_secret(
            "GOOGLE_OAUTH_SCOPES",
            "openid https://www.googleapis.com/auth/calendar.readonly")
    else:
        secrets.delete_secret("GOOGLE_OAUTH_SCOPES")


def test_scoped_provider_requires_scopes_secret(oauth_client):
    """A scoped provider (Google) refuses to start consent when <PROVIDER>_OAUTH_SCOPES is unset —
    no hardcoded fallback, an explicit error naming the secret."""
    client, _ = oauth_client
    _set_google(with_scopes=False)
    r = client.post("/api/settings/oauth/google/authorize-start", json={"account": "me"})
    assert r.status_code == 400 and "GOOGLE_OAUTH_SCOPES" in r.text


def test_scoped_provider_uses_secret_scopes(oauth_client):
    """The consent request's scope param is exactly the secret's value (accepting comma/space)."""
    client, _ = oauth_client
    _set_google()
    body = client.post("/api/settings/oauth/google/authorize-start",
                       json={"account": "me"}).json()
    q = parse_qs(urlparse(body["authorize_url"]).query)
    assert q["scope"] == ["openid https://www.googleapis.com/auth/calendar.readonly"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]  # authorize_extra rides


def test_status_exposes_scope_config(oauth_client):
    client, _ = oauth_client
    _set_google(with_scopes=False)
    provs = {p["id"]: p for p in client.get("/api/settings/oauth").json()["providers"]}
    assert provs["google"]["scoped"] is True
    assert provs["google"]["scopes_key"] == "GOOGLE_OAUTH_SCOPES"
    assert provs["google"]["scopes_set"] is False        # secret not set yet → a visible gap
    assert provs["notion"]["scoped"] is False            # fixed-scope provider
    assert provs["notion"]["scopes_set"] is True         # N/A, never a gap


def test_set_public_url_validates(oauth_client):
    client, _ = oauth_client
    assert client.put("/api/settings/oauth/public-url",
                      json={"public_url": "ftp://nope"}).status_code == 400
    r = client.put("/api/settings/oauth/public-url", json={"public_url": "https://h.ts.net/"})
    assert r.status_code == 200
    assert r.json()["public_url"] == "https://h.ts.net"           # trailing slash trimmed
    assert client.app.state.server.public_url == "https://h.ts.net"
