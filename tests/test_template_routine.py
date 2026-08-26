"""The clarification template routine (D10): the API keeps the template itself protected —
never fired, never archived, flagged on its card. Protection keys off `kind: template`, not
off the slug. The standalone new-routine wizard that copied this template's budgets/models/
traits into a session was retired in D59 (routine creation now happens from a conversation
via `create_routine`); its last producer-less remains — the `.wizard-*` workspace machinery
and `web/wizard_store.py` — were deleted in 0.230.0 (F372), so only the protection contract
remains under test here."""

import pytest
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched.web.app import create_app

TOKEN = "test-token"
TEMPLATE_SLUG = "clarification"


# ---- API protection --------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug=TEMPLATE_SLUG, kind="template")
    make_routine(slug="plain")
    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_template_cannot_be_fired_or_archived(client):
    r = client.post(f"/api/routines/{TEMPLATE_SLUG}/run")
    assert r.status_code == 403 and "template" in r.json()["detail"]
    r = client.post(f"/api/routines/{TEMPLATE_SLUG}/archive")
    assert r.status_code == 403 and "template" in r.json()["detail"]
    # the guard is template-only — an ordinary routine still archives
    assert client.post("/api/routines/plain/archive").status_code == 200


def test_protection_follows_the_declared_kind_not_the_slug(tmp_path, make_routine):
    """The guards used to compare against a hardcoded slug in five places, so a second
    template would have been silently runnable. They read `kind: template` now: a routine
    merely NAMED clarification is ordinary, and any routine declaring the kind is protected.
    """
    make_routine(slug=TEMPLATE_SLUG)          # the name, WITHOUT the marker
    make_routine(slug="other-template", kind="template")   # the marker, under another name
    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        assert c.post(f"/api/routines/{TEMPLATE_SLUG}/archive").status_code == 200
        assert c.post("/api/routines/other-template/run").status_code == 403


def test_template_flagged_protected_in_payloads(client):
    detail = client.get(f"/api/routines/{TEMPLATE_SLUG}").json()
    assert detail["protected"] is True
    assert client.get("/api/routines/plain").json()["protected"] is False
    cards = {c["slug"]: c for c in client.get("/api/routines").json()}
    assert cards[TEMPLATE_SLUG]["protected"] is True
