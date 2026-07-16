"""D13=B: a wizard clarify session is a REAL run of the protected 'clarification' routine.

With the template present, create_session lands the run at
routines_home/clarification/runs/<ts> (status stamped run_id 'clarification:<ts>', the
session routine.yaml carries the clarification slug so the engine keeps that id), and the
standard run surfaces resolve it — no dotfile bridge. Without the template (legacy deploys,
bare tests) everything stays session-local exactly as before.
"""

import json

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


def _template(server):
    d = server.routines_home / wizard_store.TEMPLATE_SLUG
    (d / "state").mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"name": "Routine clarification", "slug": wizard_store.TEMPLATE_SLUG,
         "enabled": False,
         "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"}}),
        encoding="utf-8")
    return d


def test_create_session_places_run_under_clarification(tmp_path):
    server = _server(tmp_path)
    _template(server)
    wid, ts, d = wizard_store.create_session(server, "Watch arxiv for new agent papers.")
    real = server.routines_home / wizard_store.TEMPLATE_SLUG / "runs" / ts
    assert real.is_dir()
    st = json.loads((real / "status.json").read_text(encoding="utf-8"))
    assert st["run_id"] == f"{wizard_store.TEMPLATE_SLUG}:{ts}"   # a valid, bridgeless run id
    # the engine composes run ids from the session's slug — it must be the template's
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["slug"] == wizard_store.TEMPLATE_SLUG
    assert not (d / "runs").exists()                              # nothing session-local
    assert wizard_store.clarify_run_dir(server, d, ts) == real


def test_create_session_without_template_stays_session_local(tmp_path):
    server = _server(tmp_path)
    wid, ts, d = wizard_store.create_session(server, "Watch arxiv for new agent papers.")
    local = d / "runs" / ts
    st = json.loads((local / "status.json").read_text(encoding="utf-8"))
    assert st["run_id"] == f"{wid}:{ts}"                          # unchanged legacy behaviour
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["slug"] == f"wizard-{ts}"
    assert wizard_store.clarify_run_dir(server, d, ts) == local


def test_clarify_run_resolves_on_the_standard_run_surface(tmp_path):
    """The whole point of D13=B: /api/runs/clarification:<ts> serves a clarify session's run
    like any other run — same resolution, no wizard-only route needed to see it.
    """
    server0 = _server(tmp_path)
    _template(server0)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
    }), encoding="utf-8")
    server, problems = load_server_config(cfg_path)
    assert not problems
    _wid, ts, _d = wizard_store.create_session(server, "Digest my newsletters every morning.")
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        r = client.get(f"/api/runs/{wizard_store.TEMPLATE_SLUG}:{ts}")
        assert r.status_code == 200
        body = r.json()
        assert body["routine"] == wizard_store.TEMPLATE_SLUG
        assert body["run_id"] == f"{wizard_store.TEMPLATE_SLUG}:{ts}"
        assert body["state"] == "starting"                        # the boot status, pre-engine


def test_session_inbox_redirects_clarify_run_to_workspace(tmp_path):
    """A D13=B clarify run's artifact dir is clarification/runs/<ts>, but the engine polls
    the .wizard-<ts> workspace inbox — session_inbox_dir must route there, not to
    clarification/inbox (which the live session never reads).
    """
    server = _server(tmp_path)
    _template(server)
    _wid, ts, _d = wizard_store.create_session(server, "Watch arxiv for new agent papers.")
    run_dir = server.routines_home / wizard_store.TEMPLATE_SLUG / "runs" / ts
    inbox = wizard_store.session_inbox_dir(server, run_dir)
    assert inbox == server.routines_home / f".wizard-{ts}" / "inbox"
    assert inbox != server.routines_home / wizard_store.TEMPLATE_SLUG / "inbox"


def test_session_inbox_falls_through_for_ordinary_and_legacy_runs(tmp_path):
    """Ordinary routines and legacy session-local clarify runs keep routine_dir/inbox."""
    server = _server(tmp_path)
    # ordinary routine
    ordinary = server.routines_home / "digest" / "runs" / "20260101-000000"
    ordinary.mkdir(parents=True)
    assert (wizard_store.session_inbox_dir(server, ordinary)
            == server.routines_home / "digest" / "inbox")
    # legacy session-local clarify run (no template → stays under .wizard-<ts>)
    _wid, ts, d = wizard_store.create_session(server, "Digest my newsletters.")
    local = d / "runs" / ts
    assert wizard_store.session_inbox_dir(server, local) == d / "inbox"


def test_inject_endpoint_reaches_the_clarify_session_workspace(tmp_path):
    """POST /api/runs/clarification:<ts>/inject must land the message in the .wizard-<ts>
    workspace inbox (polled by the live session), not clarification/inbox.
    """
    server0 = _server(tmp_path)
    _template(server0)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
    }), encoding="utf-8")
    server, problems = load_server_config(cfg_path)
    assert not problems
    _wid, ts, _d = wizard_store.create_session(server, "Digest my newsletters every morning.")
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        r = client.post(f"/api/runs/{wizard_store.TEMPLATE_SLUG}:{ts}/inject",
                        json={"text": "focus on multi-agent papers"})
        assert r.status_code == 200
        workspace_inbox = server.routines_home / f".wizard-{ts}" / "inbox"
        msgs = list(workspace_inbox.glob("msg-*.json"))
        assert len(msgs) == 1
        assert json.loads(msgs[0].read_text(encoding="utf-8"))["via"] == "web"
        template_inbox = server.routines_home / wizard_store.TEMPLATE_SLUG / "inbox"
        assert not (template_inbox.is_dir() and list(template_inbox.glob("msg-*.json")))
