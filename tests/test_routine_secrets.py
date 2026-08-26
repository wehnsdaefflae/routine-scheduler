"""Routine-scoped secrets (D103, operator decision 2026-08-26 — R497): the second store
scope, its injection into a run's util subprocesses, its exemption from the exposure gate,
and the write API.

The invariant under test throughout: a scoped secret is OWNED by its routine — implicitly
exposed to its runs, invisible to every other routine, shadowing a central value of the same
name — while the declared-only rule (a util gets a var only if its header names it) is
untouched by any of that.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched import secrets
from rsched.web.app import create_app


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    """Both scopes into tmp: scoped_path derives from secrets_path, so one patch moves both."""
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "store" / "secrets.env")


# ---- the store -------------------------------------------------------------------------


def test_scoped_store_is_separate_from_the_central_one():
    secrets.set_secret("SFTP_USER", "shared-value")
    secrets.set_routine_secret("eye-stabilize-folder", "SFTP_USER", "mine")
    assert secrets.load_secrets() == {"SFTP_USER": "shared-value"}      # central untouched
    assert secrets.load_routine_secrets("eye-stabilize-folder") == {"SFTP_USER": "mine"}
    assert secrets.load_routine_secrets("other-routine") == {}          # nobody else's


def test_scoped_stores_live_beside_config_never_in_the_routine_dir(tmp_path):
    """A routine dir is `git add -A` autocommitted and auto-pushed — a credential written
    there would leave the host. The store belongs beside config.yaml."""
    secrets.set_routine_secret("alpha", "TOKEN", "t")
    path = secrets.scoped_path("alpha")
    assert path == tmp_path / "store" / "secrets.d" / "alpha.env"
    assert path.read_text(encoding="utf-8") == "TOKEN=t\n"


def test_a_crafted_slug_cannot_escape_the_directory():
    for bad in ("../evil", "a/b", "", "Not A Slug"):
        with pytest.raises(ValueError, match="valid routine slug"):
            secrets.scoped_path(bad)


def test_multiline_values_round_trip_like_the_central_store():
    pem = "-----BEGIN KEY-----\nline-two\n-----END KEY-----\n"
    secrets.set_routine_secret("alpha", "SFTP_KEY", pem)
    assert secrets.load_routine_secrets("alpha")["SFTP_KEY"] == pem


def test_delete_and_drop():
    secrets.set_routine_secret("alpha", "A", "1")
    secrets.set_routine_secret("alpha", "B", "2")
    assert secrets.delete_routine_secret("alpha", "A") is True
    assert secrets.delete_routine_secret("alpha", "A") is False        # already gone
    assert secrets.routine_secret_keys("alpha") == ["B"]
    assert secrets.drop_routine_secrets("alpha") is True               # the whole store
    assert secrets.routine_secret_keys("alpha") == []
    assert secrets.drop_routine_secrets("alpha") is False


# ---- injection + the exposure gate ------------------------------------------------------


def _ctx(make_routine, slug="scoped"):
    from types import SimpleNamespace

    d = make_routine(slug=slug)
    return SimpleNamespace(routine=SimpleNamespace(slug=slug, dir=d, connections={},
                                                   machines=[]),
                           server=SimpleNamespace(machines={}, routine_token=""))


def test_the_run_offers_its_own_secrets_as_engine_extras(make_routine):
    """`_routine_secrets` is the engine-side half: a run contributes its scoped store to the
    same extra_secrets channel connection tokens and machine keys ride."""
    from rsched.engine.executor import _routine_secrets

    secrets.set_secret("SFTP_USER", "shared")
    secrets.set_routine_secret("scoped", "SFTP_USER", "mine")
    assert _routine_secrets(_ctx(make_routine)) == {"SFTP_USER": "mine"}


def test_a_routine_without_a_scoped_store_contributes_nothing(make_routine):
    from rsched.engine.executor import _routine_secrets

    secrets.set_secret("SFTP_USER", "shared")
    assert _routine_secrets(_ctx(make_routine)) == {}


def test_a_scoped_value_shadows_the_central_one_in_the_child_env():
    """The shadowing rule, end to end through the real injection function: extra_secrets win
    the merge, so the routine's own value is what the util subprocess is handed."""
    from rsched.utils_lib import scoped_env

    secrets.set_secret("SFTP_USER", "shared")
    env = scoped_env({"SFTP_USER"}, {"SFTP_USER": "mine"})
    assert env["SFTP_USER"] == "mine"


def test_declared_only_still_governs_a_scoped_secret():
    """Ownership removes the exposure DECISION, never the declared-only rule: a util that
    does not name the var on its `secrets:` header still never sees it."""
    from rsched.utils_lib import scoped_env

    env = scoped_env({"SFTP_USER"}, {"SFTP_USER": "mine", "SFTP_PASS": "undeclared"})
    assert env["SFTP_USER"] == "mine"
    assert "SFTP_PASS" not in env


def test_the_exposure_gate_skips_a_name_the_routine_owns(make_routine):
    """A name in BOTH stores must not file an access request: the run will be handed its
    OWN value, so asking to be shown the shared one is a question about nothing."""
    from rsched.engine.interact import _own_secrets

    secrets.set_secret("SFTP_USER", "shared")
    secrets.set_routine_secret("scoped", "SFTP_USER", "mine")
    ctx = _ctx(make_routine)
    assert _own_secrets(ctx) == {"SFTP_USER"}

    required = {"SFTP_USER"}
    present = sorted((required & set(secrets.load_secrets())) - _own_secrets(ctx))
    assert present == []          # nothing left to gate


# ---- the API ----------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")
    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c, tmp_path


def test_api_round_trip_returns_names_never_values(client):
    c, _tmp = client
    assert c.put("/api/routines/apir/secrets",
                 json={"key": "SFTP_PASS", "value": "hunter2"}).status_code == 200
    body = c.get("/api/routines/apir/secrets").json()
    assert body["keys"] == ["SFTP_PASS"]
    assert "hunter2" not in c.get("/api/routines/apir/secrets").text
    assert c.delete("/api/routines/apir/secrets/SFTP_PASS").json()["keys"] == []
    assert c.delete("/api/routines/apir/secrets/SFTP_PASS").status_code == 404


def test_api_reports_which_central_names_are_shadowed(client):
    c, _tmp = client
    secrets.set_secret("SFTP_USER", "shared")
    secrets.set_secret("UNRELATED", "x")
    c.put("/api/routines/apir/secrets", json={"key": "SFTP_USER", "value": "mine"})
    c.put("/api/routines/apir/secrets", json={"key": "OWN_ONLY", "value": "y"})
    body = c.get("/api/routines/apir/secrets").json()
    assert body["shadowing"] == ["SFTP_USER"]     # OWN_ONLY collides with nothing


def test_api_rejects_an_invalid_env_var_name(client):
    c, _tmp = client
    r = c.put("/api/routines/apir/secrets", json={"key": "not a var", "value": "v"})
    assert r.status_code == 400 and "environment variable name" in r.json()["detail"]


def test_api_refuses_an_unknown_routine(client):
    c, _tmp = client
    assert c.put("/api/routines/ghost/secrets",
                 json={"key": "K", "value": "v"}).status_code == 404


def test_archiving_a_routine_drops_its_secrets(client):
    """A credential must not outlive the only thing entitled to it — nor be inherited by a
    later routine that happens to reuse the slug."""
    c, _tmp = client
    c.put("/api/routines/apir/secrets", json={"key": "SFTP_PASS", "value": "hunter2"})
    assert secrets.scoped_path("apir").exists()
    r = c.post("/api/routines/apir/archive")
    assert r.status_code == 200 and r.json()["secrets_dropped"] is True
    assert not secrets.scoped_path("apir").exists()
