"""The /api/llm-tasks reconcile route + its app wiring: the overlay fetches this snapshot on
boot and after an SSE reconnect. Also asserts the daemon sink is installed for the app's life."""

import pytest

from rsched.endpoints.instrument import get_sink
from rsched.llm_tasks import DaemonSink


@pytest.fixture
def client(api_client):
    return api_client[0]


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
