"""The /api/llm-tasks reconcile route + its app wiring: the overlay fetches this snapshot on
boot and after an SSE reconnect. Also asserts the daemon sink is installed for the app's life."""

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched.config import load_server_config
from rsched.endpoints.instrument import get_sink
from rsched.llm_tasks import DaemonSink
from rsched.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "system_model": {"endpoint": "dummy", "model": "m"},
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_requires_auth(client):
    assert client.get("/api/llm-tasks", headers={"Authorization": ""}).status_code == 401


def test_daemon_sink_installed_during_app_life(client):
    # the lifespan wires the process-global sink so instrumented complete() calls are observed
    assert isinstance(get_sink(), DaemonSink)


def test_empty_snapshot(client):
    snap = client.get("/api/llm-tasks").json()
    assert snap == {"processes": [], "tasks": []}


def test_snapshot_reflects_center(client):
    center = client.app.state.llm_tasks
    center.open_process("create:abc", kind="wizard", label="Create routine: Foo")
    center.ingest({"id": "t1", "phase": "started", "endpoint": "anthropic", "model": "opus",
                   "purpose": "Decompose workflow", "process_id": "create:abc"})
    center.ingest({"id": "t2", "phase": "finished", "endpoint": "openai", "model": "gpt",
                   "purpose": "Test endpoint"})  # a standalone one-off

    snap = client.get("/api/llm-tasks").json()
    assert [p["id"] for p in snap["processes"]] == ["create:abc"]
    by_id = {t["id"]: t for t in snap["tasks"]}
    assert by_id["t1"]["status"] == "running" and by_id["t1"]["process_id"] == "create:abc"
    assert by_id["t2"]["status"] == "done" and "process_id" not in by_id["t2"]
