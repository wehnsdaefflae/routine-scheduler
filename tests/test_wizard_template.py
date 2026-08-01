"""The clarification template routine (D10): the API keeps the template itself protected —
never fired, never archived, flagged on its card. The standalone new-routine wizard that
copied this template's budgets/models/traits into a session was retired in D59 (routine
creation now happens from a conversation via `create_routine`), so only the protection
contract remains under test here."""

import pytest
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched.web import wizard_store
from rsched.web.app import create_app

TOKEN = "test-token"


# ---- API protection --------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug=wizard_store.TEMPLATE_SLUG)
    make_routine(slug="plain")
    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_template_cannot_be_fired_or_archived(client):
    r = client.post(f"/api/routines/{wizard_store.TEMPLATE_SLUG}/run")
    assert r.status_code == 403 and "template" in r.json()["detail"]
    r = client.post(f"/api/routines/{wizard_store.TEMPLATE_SLUG}/archive")
    assert r.status_code == 403 and "template" in r.json()["detail"]
    # the guard is template-only — an ordinary routine still archives
    assert client.post("/api/routines/plain/archive").status_code == 200


def test_template_flagged_protected_in_payloads(client):
    detail = client.get(f"/api/routines/{wizard_store.TEMPLATE_SLUG}").json()
    assert detail["protected"] is True
    assert client.get("/api/routines/plain").json()["protected"] is False
    cards = {c["slug"]: c for c in client.get("/api/routines").json()}
    assert cards[wizard_store.TEMPLATE_SLUG]["protected"] is True
