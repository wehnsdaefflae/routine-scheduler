"""What both credential-screen test modules share: the seeded credential values, the factories
that build a seeded instance and a client over it, and the two request helpers.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched import secrets
from rsched.oauth import store as conn_store
from rsched.web.app import create_app

ROUTINE_TOKEN = "routine-tok"

CENTRAL_VALUE = "central-value-9f3a1c7e"
SCOPED_VALUE = "scoped-value-5b2d8e04"
ACCESS_VALUE = "access-token-value-c4e7b2"
MULTILINE_VALUE = "-----BEGIN KEY-----\nline-two-7d41bc\n-----END KEY-----"
#: Every seeded value, for the by-value assertion that no response carries one.
ALL_VALUES = (CENTRAL_VALUE, SCOPED_VALUE, ACCESS_VALUE, MULTILINE_VALUE)


def seed_stores(monkeypatch, tmp_path: Path) -> Path:
    """Point every credential source at tmp and seed each with a distinctive value."""
    store_dir = tmp_path / "store"
    monkeypatch.setattr(secrets, "secrets_path", lambda: store_dir / "secrets.env")
    monkeypatch.setattr(conn_store, "connections_path",
                        lambda: store_dir / "connections.json")
    secrets.set_secret("CENTRAL_API_KEY", CENTRAL_VALUE)
    secrets.set_secret("MY_PRIVATE_KEY", MULTILINE_VALUE)
    secrets.set_routine_secret("alpha", "ALPHA_TOKEN", SCOPED_VALUE)
    conn_store.set_connection(conn_store.Connection(
        provider="notion", account="work", access_token=ACCESS_VALUE))
    return store_dir


def seeded_client(tmp_path: Path, make_routine) -> TestClient:
    """A client over a server whose routines home is tmp's, with the routine token set so the
    tier the export actually runs under is exercisable."""
    make_routine(slug="alpha")
    server = make_test_server(tmp_path, routine_token=ROUTINE_TOKEN)
    return TestClient(create_app(server, with_scheduler=False))


def inside_root(tmp_path: Path) -> Path:
    """A directory inside the authorized routines home."""
    d = tmp_path / "routines" / "alpha" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def screen_paths(client, *paths, token=TEST_TOKEN):
    """GET the screen for each path, as the export's own caller would."""
    return client.get("/api/credential-screen",
                      params=[("path", str(p)) for p in paths],
                      headers={"Authorization": f"Bearer {token}"})


def screen_one(client, path, token=TEST_TOKEN):
    """The single result of a one-path screen, asserting the call was admitted at all."""
    body = screen_paths(client, path, token=token).json()
    assert body["screened"], body
    return body["screened"][0]
