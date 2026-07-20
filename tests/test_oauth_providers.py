"""OAuth provider registry: the Notion entry is load-bearing (auth-code, non-expiring, no
device flow), and client creds resolve from the central Secrets store."""

from __future__ import annotations

from rsched import secrets
from rsched.oauth import providers


def test_notion_provider_shape():
    p = providers.get_provider("notion")
    assert p is not None
    assert p.uses_pkce is True
    assert p.expiring is False          # long-lived bearer → the refresh manager skips it
    assert p.device_url is None         # no device flow → auth-code + callback only
    assert p.authorize_url.startswith("https://api.notion.com/")
    assert p.token_url.startswith("https://api.notion.com/")


def test_registry_ids_and_unknown():
    ids = providers.provider_ids()
    assert "notion" in ids and ids == sorted(ids)
    assert providers.get_provider("does-not-exist") is None


def test_client_creds_from_secrets():
    assert providers.client_creds("notion") is None            # unset → None
    secrets.set_secret("NOTION_OAUTH_CLIENT_ID", "cid-123")
    creds = providers.client_creds("notion")
    assert creds is not None
    assert creds.client_id == "cid-123"
    assert creds.client_secret == ""                           # PKCE client, secret optional
    secrets.set_secret("NOTION_OAUTH_CLIENT_SECRET", "shh")
    later = providers.client_creds("notion")
    assert later is not None and later.client_secret == "shh"


def test_creds_secret_keys():
    assert providers.creds_secret_keys("notion") == (
        "NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET")


def test_connection_token_vars():
    assert providers.access_token_var("notion") == "NOTION_ACCESS_TOKEN"
    every = providers.connection_token_vars()
    assert "NOTION_ACCESS_TOKEN" in every
    assert every == {f"{pid.upper()}_ACCESS_TOKEN" for pid in providers.provider_ids()}
