"""The clarification template routine (D10): every wizard session copies its budgets,
models, and traits/ from routines_home/clarification when it exists, and the API keeps
the template itself protected — never fired, never archived, flagged on its card."""

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched.config import ServerConfig
from rsched.web import wizard_store
from rsched.web.app import create_app

TOKEN = "test-token"


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    s.libraries_home = tmp_path / "library"   # absent → the candidate list is simply empty
    return s


def _template(server, *, budgets=None, models=None, traits=None):
    d = server.routines_home / wizard_store.TEMPLATE_SLUG
    (d / "state").mkdir(parents=True)
    cfg = {"name": "Routine clarification", "slug": wizard_store.TEMPLATE_SLUG,
           "enabled": False,
           "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"}}
    if budgets:
        cfg["budgets"] = budgets
    if models:
        cfg["models"] = models
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    for name, body in (traits or {}).items():
        (d / "traits").mkdir(exist_ok=True)
        (d / "traits" / name).write_text(body, encoding="utf-8")
    return d


# ---- template_defaults ---------------------------------------------------------------


def test_defaults_without_template_fall_back_to_hardcoded(tmp_path):
    server = _server(tmp_path)
    budgets, models, level = wizard_store.template_defaults(server)
    assert budgets == wizard_store.WIZARD_BUDGETS
    assert budgets is not wizard_store.WIZARD_BUDGETS   # a copy — callers may mutate
    assert models == {}
    assert level == ""                                  # "" = the config default


def test_defaults_overlay_template_budgets_and_models(tmp_path):
    server = _server(tmp_path)
    _template(server, budgets={"max_turns": 60}, models={"main": "m"})
    budgets, models, level = wizard_store.template_defaults(server)
    assert budgets["max_turns"] == 60                       # the template's value wins
    assert budgets["max_wall_clock_min"] == \
        wizard_store.WIZARD_BUDGETS["max_wall_clock_min"]   # omitted keys stay complete
    assert models == {"main": "m"}
    assert level == ""                                  # template without the key → default


def test_defaults_survive_a_broken_template_yaml(tmp_path):
    server = _server(tmp_path)
    d = server.routines_home / wizard_store.TEMPLATE_SLUG
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text(":: not yaml ::", encoding="utf-8")
    budgets, models, level = wizard_store.template_defaults(server)
    assert budgets == wizard_store.WIZARD_BUDGETS
    assert models == {}
    assert level == ""


# ---- create_session copies the template ------------------------------------------------


def test_create_session_copies_budgets_models_and_traits(tmp_path):
    server = _server(tmp_path)
    _template(server, budgets={"max_turns": 60}, models={"main": "m"},
              traits={"ask-policy.md": "# ask policy\n"})
    _wid, _ts, d = wizard_store.create_session(server, "Watch arxiv for new agent papers.")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["budgets"]["max_turns"] == 60
    assert raw["budgets"]["ask_timeout_min"] == wizard_store.WIZARD_BUDGETS["ask_timeout_min"]
    assert raw["models"] == {"main": "m"}
    assert (d / "traits" / "ask-policy.md").read_text(encoding="utf-8") == "# ask policy\n"


def test_create_session_without_template_matches_old_behaviour(tmp_path):
    server = _server(tmp_path)
    _wid, _ts, d = wizard_store.create_session(server, "Watch arxiv for new agent papers.")
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["budgets"] == wizard_store.WIZARD_BUDGETS
    assert "models" not in raw
    assert not (d / "traits").exists()


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


# ---- finalize models inheritance (F153) ----------------------------------------------


def test_build_models_stay_unset_when_wizard_sends_none(tmp_path):
    """No explicit models pick on finalize → the routine's models stay UNSET (system
    default). SUPERSEDES the 2026-07-23 template-inherit contract: inheriting the
    clarify template's models silently made the clarify-session model every created
    routine's configured model (F191); the create form now carries an explicit model
    picker for the deliberate case, so nothing is set behind the user's back."""
    from rsched.web.api_wizard import _models_for_build
    server = _server(tmp_path)
    _template(server, models={"main": "m", "subroutine": "m", "tool_call": "m"})
    assert _models_for_build(server, None) is None
    assert _models_for_build(server, {"main": "x"}) == {"main": "x"}  # explicit pick wins


def test_build_models_without_template_stay_none(tmp_path):
    from rsched.web.api_wizard import _models_for_build
    assert _models_for_build(_server(tmp_path), None) is None


def test_models_for_build_never_inherits_template_models():
    """F191: the clarification template's models are the CLARIFY session's choice — they
    must not silently become a created routine's configured models. Only an explicit
    wizard pick (FinalizeBody.models) sets them; otherwise None → system-model fallback."""
    from rsched.web.api_wizard import _models_for_build

    class _Server:   # would previously have been consulted for the template's models
        pass

    assert _models_for_build(_Server(), None) is None
    assert _models_for_build(_Server(), {}) is None
    picked = {"main": "Fable", "subroutine": "Fable", "tool_call": "Fable"}
    assert _models_for_build(_Server(), picked) == picked
