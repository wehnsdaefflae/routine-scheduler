"""The clarification template routine (D10): every wizard session copies its budgets,
models, and traits/ from routines_home/clarification when it exists, and the API keeps
the template itself protected — never fired, never archived, flagged on its card."""

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched.config import ServerConfig, load_server_config
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
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
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
