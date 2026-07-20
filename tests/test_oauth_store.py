"""Connection store: round-trips a connection at mode 0600, exposes metadata-only listings, and
`tokens_for_routine` resolves bindings into `<PROVIDER>_ACCESS_TOKEN` env vars (skipping any that
are missing or need re-auth)."""

from __future__ import annotations

import pytest

from rsched.oauth import store
from rsched.oauth.store import Connection


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "connections_path", lambda: tmp_path / "connections.json")


def test_set_get_roundtrip():
    store.set_connection(Connection(provider="notion", account="acme", access_token="tok",
                                    label="ACME Inc"))
    got = store.get_connection("notion", "acme")
    assert got is not None
    assert got.access_token == "tok"
    assert got.label == "ACME Inc"


def test_list_is_metadata_only():
    store.set_connection(Connection(provider="notion", account="acme", access_token="secret-tok",
                                    refresh_token="rt"))
    listed = store.list_connections()
    assert listed == [{
        "provider": "notion", "account": "acme", "label": "", "scopes": [],
        "expires_at": 0.0, "obtained_at": 0.0, "needs_reauth": False, "has_refresh": True}]
    for row in listed:                                  # tokens NEVER surface
        assert "access_token" not in row
        assert "refresh_token" not in row


def test_file_is_0600(tmp_path):
    store.set_connection(Connection(provider="notion", account="a", access_token="t"))
    assert (tmp_path / "connections.json").stat().st_mode & 0o777 == 0o600


def test_delete():
    store.set_connection(Connection(provider="notion", account="a", access_token="t"))
    assert store.delete_connection("notion", "a") is True
    assert store.get_connection("notion", "a") is None
    assert store.delete_connection("notion", "a") is False    # idempotent


def test_forward_compat_unknown_keys(tmp_path):
    (tmp_path / "connections.json").write_text(
        '{"notion:a": {"provider": "notion", "account": "a", "access_token": "t", '
        '"future_field": 1}}', encoding="utf-8")
    got = store.get_connection("notion", "a")
    assert got is not None and got.access_token == "t"


def test_invalid_key_rejected():
    with pytest.raises(ValueError, match="invalid provider/account"):
        store.set_connection(Connection(provider="Notion Bad", account="a"))


def test_tokens_for_routine():
    store.set_connection(Connection(provider="notion", account="acme", access_token="AT"))
    store.set_connection(Connection(provider="google", account="me", access_token="",
                                    needs_reauth=True))
    env, warnings = store.tokens_for_routine({"notion": "acme", "google": "me", "slack": "x"})
    assert env == {"NOTION_ACCESS_TOKEN": "AT"}
    assert "GOOGLE_ACCESS_TOKEN" not in env          # needs_reauth → skipped
    assert "SLACK_ACCESS_TOKEN" not in env           # not connected → skipped
    assert any("google:me" in w for w in warnings)
    assert any("slack:x" in w for w in warnings)


def test_tokens_for_routine_empty():
    assert store.tokens_for_routine({}) == ({}, [])
