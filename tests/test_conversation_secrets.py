"""A conversation's OWN secrets (D216 rank 1 — the operator: "All six, as one parity campaign").

The point under test is a PARITY, not a new mechanism: the scoped store has always been keyed by
the run's slug, and a conversation's slug is its directory name exactly as a routine's is, so the
engine already injected `secrets.d/<conversation-slug>.env` into its util calls. Only the write
surface was missing — `api_routine_secrets` resolves through `routines_common._info`, which asks
`registry.info` about `routines_home` alone, so a conversation slug 404'd on a store that would
have served it.

So each test here asserts the conversation behaves like a routine on the SAME store, including the
two refusals that matter: an unknown slug cannot create an orphan credentials file, and the names
read is refused to the routine token.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, authed_client, make_test_server
from rsched import secrets
from rsched.web.app import create_app


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    """Both scopes into tmp: scoped_path derives from secrets_path, so one patch moves both."""
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "store" / "secrets.env")


def _server(tmp_path, **overrides):
    """make_test_server does NOT override `conversations_home` — it resolves to the REAL one
    (`/home/mark/conversations` on the deployment), so a test that creates a conversation
    there either writes into the live home or dies on Errno 13 depending on the sandbox it
    runs in. Always name a tmp home, as `conftest.seeded_server` does.
    """
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            **overrides)


def _conversation(server, slug: str = "c-20261028-120000") -> str:
    """The minimum on-disk shape `registry.info` reads as a conversation."""
    conv = server.conversations_home / slug
    conv.mkdir(parents=True, exist_ok=True)
    (conv / "routine.yaml").write_text(f"slug: {slug}\nname: a conversation\n", encoding="utf-8")
    (conv / "main.md").write_text("# converse\n", encoding="utf-8")
    return slug


@pytest.fixture
def conv(tmp_path):
    server = _server(tmp_path)
    slug = _conversation(server)
    with authed_client(server) as c:
        yield c, slug


def test_a_conversation_round_trips_its_own_secret_and_never_echoes_the_value(conv):
    c, slug = conv
    assert c.put(f"/api/conversations/{slug}/secrets",
                 json={"key": "SFTP_PASS", "value": "hunter2"}).json()["keys"] == ["SFTP_PASS"]
    body = c.get(f"/api/conversations/{slug}/secrets").json()
    assert body["keys"] == ["SFTP_PASS"]
    assert "hunter2" not in c.get(f"/api/conversations/{slug}/secrets").text
    assert c.delete(f"/api/conversations/{slug}/secrets/SFTP_PASS").json()["keys"] == []
    assert c.delete(f"/api/conversations/{slug}/secrets/SFTP_PASS").status_code == 404


def test_it_is_the_same_store_the_engine_already_injects(conv):
    """The parity claim itself: what the route writes is exactly what
    `secrets.load_routine_secrets(slug)` — the reader `engine/capabilities` and
    `engine/secretgate._own_secrets` use — serves to that conversation's runs.
    """
    c, slug = conv
    c.put(f"/api/conversations/{slug}/secrets", json={"key": "TOKEN", "value": "t"})
    assert secrets.load_routine_secrets(slug) == {"TOKEN": "t"}
    assert secrets.scoped_path(slug).name == f"{slug}.env"


def test_shadowing_is_reported_the_way_the_routine_side_reports_it(conv):
    c, slug = conv
    secrets.set_secret("SFTP_USER", "shared")
    c.put(f"/api/conversations/{slug}/secrets", json={"key": "SFTP_USER", "value": "mine"})
    c.put(f"/api/conversations/{slug}/secrets", json={"key": "OWN_ONLY", "value": "y"})
    body = c.get(f"/api/conversations/{slug}/secrets").json()
    assert body["shadowing"] == ["SFTP_USER"]
    assert body["keys"] == ["OWN_ONLY", "SFTP_USER"]


def test_an_unknown_conversation_cannot_create_an_orphan_credentials_file(conv):
    """A typo'd slug must 404 rather than write `secrets.d/<typo>.env` full of credentials."""
    c, _ = conv
    assert c.put("/api/conversations/c-does-not-exist/secrets",
                 json={"key": "A", "value": "1"}).status_code == 404
    assert not secrets.scoped_path("c-does-not-exist").exists()


def test_a_routine_slug_is_not_reachable_through_the_conversation_route(conv, make_routine):
    """The two surfaces stay separate: this route resolves against conversations_home only, so
    it cannot be used to write another routine's store."""
    c, _ = conv
    make_routine("apir")
    assert c.put("/api/conversations/apir/secrets",
                 json={"key": "A", "value": "1"}).status_code == 404


def test_the_names_read_is_refused_to_the_routine_token(tmp_path):
    """Otherwise a run could read every conversation's secret names with the token it carries —
    the same reason `/api/routines/*/secrets` is in ROUTINE_TOKEN_DENIED_READS.
    """
    server = _server(tmp_path, routine_token="routine-tok")
    slug = _conversation(server)
    secrets.set_routine_secret(slug, "SFTP_PASS", "hunter2")
    with TestClient(create_app(server)) as c:
        r = c.get(f"/api/conversations/{slug}/secrets",
                  headers={"Authorization": "Bearer routine-tok"})
        assert r.status_code == 403
        ok = c.get(f"/api/conversations/{slug}/secrets",
                   headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert ok.status_code == 200


def test_an_invalid_var_name_is_refused(conv):
    c, slug = conv
    r = c.put(f"/api/conversations/{slug}/secrets", json={"key": "not a var", "value": "v"})
    assert r.status_code == 400
