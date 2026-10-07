"""The agent-desktop fleet as the console sees it: `/api/desktops` and its stop route.

The broker keeps its fleet view behind a second secret no routine is granted, because the view
carries each desktop's screen token — the key to a keyboard on that routine's computer. These
tests pin that the console does not undo that on its own side: the routes ask the broker with
BOTH credentials, and a run's RSCHED_API_TOKEN can neither read the fleet nor stop a desktop,
nor reach the relayed screens.
"""

from __future__ import annotations

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched.web import api_desktops
from rsched.web.app import create_app

ROUTINE_TOKEN = "routine-tok"
OPERATOR = {"Authorization": f"Bearer {TEST_TOKEN}"}
AS_RUN = {"Authorization": f"Bearer {ROUTINE_TOKEN}"}
SCREEN = "0123456789abcdef0123456789abcdef"
SECRETS = {"DESKTOP_VM_TOKEN": "vm-tok", "DESKTOP_OPERATOR_TOKEN": "op-tok"}

FLEET = {"slots": 2, "idle_limit_s": 1200, "desktops": [
    {"name": "routines--alpha", "slot": 0, "ready": True, "stopping": False, "up_s": 50,
     "idle_s": 30, "shares": ["data"], "vnc": SCREEN,
     "folders": [{"name": "data", "path": "/home/mark/data", "rw": False,
                  "guest_path": "/home/mark/mnt/data"}]},
    {"name": "conversations--chat", "slot": 1, "ready": True, "stopping": False, "up_s": 9,
     "idle_s": 2, "shares": [], "vnc": "not-a-token", "folders": []},
    {"name": "background--bg1", "slot": 2, "ready": False, "stopping": True, "up_s": 1,
     "idle_s": 1, "shares": [], "vnc": SCREEN.replace("0", "f"), "folders": []},
]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: dict(SECRETS))
    server = make_test_server(tmp_path, routine_token=ROUTINE_TOKEN,
                              background_home=str(tmp_path / "background"),
                              desktop_broker_url="http://172.30.7.20:8790/",
                              desktop_view_url="http://172.30.7.20:6080/vnc.html")
    with TestClient(create_app(server, with_scheduler=False)) as c:
        yield c


def _broker(monkeypatch, *, status: int = 200, body: object = None,
            error: Exception | None = None) -> list[dict]:
    """Replace the route's one outbound client; return every request it was asked to make."""
    calls: list[dict] = []

    class _Client:
        def __init__(self, *_a, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": dict(headers or {}),
                          **self.kwargs})
            if error is not None:
                raise error
            return httpx.Response(status, json=FLEET if body is None else body,
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(api_desktops.httpx, "AsyncClient", _Client)
    return calls


def test_the_fleet_is_asked_for_with_both_credentials(client, monkeypatch):
    calls = _broker(monkeypatch)
    r = client.get("/api/desktops", headers=OPERATOR)
    assert r.status_code == 200, r.text
    (call,) = calls
    assert call["url"] == "http://172.30.7.20:8790/fleet"
    assert call["json"] == {}
    assert call["headers"]["Authorization"] == "Bearer vm-tok"
    assert call["headers"]["X-Desktop-Operator"] == "op-tok"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["follow_redirects"] is False


def test_each_desktop_is_named_after_its_owner_and_linked_to_it(client, monkeypatch, tmp_path):
    task = tmp_path / "background" / "bg1"
    task.mkdir(parents=True)
    (task / "routine.yaml").write_text(yaml.safe_dump({"owner": {"slug": "chat"}}),
                                       encoding="utf-8")
    _broker(monkeypatch)
    body = client.get("/api/desktops", headers=OPERATOR).json()
    assert body["slots"] == 2 and body["idle_limit_s"] == 1200
    rows = {d["name"]: d for d in body["desktops"]}
    assert rows["routines--alpha"]["owner"] == {"kind": "routine", "slug": "alpha",
                                                "href": "#/routine/alpha"}
    assert rows["conversations--chat"]["owner"]["href"] == "#/conversations/chat"
    # a background task's desktop links to the conversation that launched it
    assert rows["background--bg1"]["owner"] == {"kind": "background", "slug": "bg1",
                                                "href": "#/conversations/chat"}
    assert rows["routines--alpha"]["vnc"] == SCREEN
    assert rows["routines--alpha"]["folders"][0]["guest_path"] == "/home/mark/mnt/data"
    # a token the relay would refuse is dropped, not handed to a frame that fails later
    assert rows["conversations--chat"]["vnc"] == ""
    # most recently active first
    assert [d["name"] for d in body["desktops"]] == [
        "background--bg1", "conversations--chat", "routines--alpha"]


def test_a_broker_that_does_not_answer_is_a_502_not_an_empty_fleet(client, monkeypatch):
    _broker(monkeypatch, error=httpx.ConnectError("connection refused"))
    r = client.get("/api/desktops", headers=OPERATOR)
    assert r.status_code == 502 and "did not answer" in r.text


def test_a_broker_refusal_is_reported_with_what_it_said(client, monkeypatch):
    _broker(monkeypatch, status=403, body={"error": "it needs X-Desktop-Operator"})
    r = client.get("/api/desktops", headers=OPERATOR)
    assert r.status_code == 502
    assert "403" in r.text and "X-Desktop-Operator" in r.text


def test_missing_secrets_are_named(client, monkeypatch):
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"DESKTOP_VM_TOKEN": "vm"})
    calls = _broker(monkeypatch)
    r = client.get("/api/desktops", headers=OPERATOR)
    assert r.status_code == 503 and "DESKTOP_OPERATOR_TOKEN" in r.text
    assert calls == []


def test_an_unconfigured_broker_is_a_503_naming_the_field(client, monkeypatch):
    client.app.state.server.desktop_broker_url = ""
    calls = _broker(monkeypatch)
    r = client.get("/api/desktops", headers=OPERATOR)
    assert r.status_code == 503 and "desktop_broker_url" in r.text
    assert calls == []


def test_stop_names_one_desktop_to_the_broker(client, monkeypatch):
    calls = _broker(monkeypatch, body={"stopped": True})
    r = client.post("/api/desktops/routines--alpha/stop", headers=OPERATOR)
    assert r.status_code == 200 and r.json() == {"ok": True, "stopped": True}
    assert calls[0]["url"] == "http://172.30.7.20:8790/fleet_stop"
    assert calls[0]["json"] == {"name": "routines--alpha"}
    assert calls[0]["headers"]["X-Desktop-Operator"] == "op-tok"


@pytest.mark.parametrize("name", ["alpha", "machines--x", "routines--", "routines--a b"])
def test_stop_refuses_what_is_not_a_desktop_name(client, monkeypatch, name):
    calls = _broker(monkeypatch)
    assert client.post(f"/api/desktops/{name}/stop", headers=OPERATOR).status_code in (400, 404)
    assert calls == []


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/desktops"),
    ("POST", "/api/desktops/routines--alpha/stop"),
    ("POST", "/api/desktop-view/pass"),
    ("GET", "/desktop-view/vnc.html"),
    ("GET", "/browser-view/vnc.html"),
])
def test_a_runs_token_reaches_neither_the_fleet_nor_a_screen(client, monkeypatch, method, path):
    calls = _broker(monkeypatch)
    r = client.request(method, path, headers=AS_RUN)
    assert r.status_code == 403, (path, r.status_code, r.text[:120])
    assert r.headers.get("www-authenticate", "").endswith('error="insufficient_scope"')
    assert calls == []


def test_status_says_whether_the_desktops_are_configured(client):
    status = client.get("/api/status", headers=OPERATOR).json()
    assert status["desktops"] is True
    assert status["desktop_view_url"] == "http://172.30.7.20:6080/vnc.html"
    client.app.state.server.desktop_view_url = ""
    assert client.get("/api/status", headers=OPERATOR).json()["desktops"] is False


def test_the_two_addresses_round_trip_through_settings(client, tmp_path):
    r = client.put("/api/settings/server", headers=OPERATOR, json={
        "desktop_broker_url": "http://10.1.2.3:8790/",
        "desktop_view_url": " http://10.1.2.3:6080/vnc.html "})
    assert r.status_code == 200, r.text
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert raw["desktop_broker_url"] == "http://10.1.2.3:8790"
    assert raw["desktop_view_url"] == "http://10.1.2.3:6080/vnc.html"
    got = client.get("/api/settings/server", headers=OPERATOR).json()
    assert got["desktop_broker_url"] == "http://10.1.2.3:8790"
    refused = client.put("/api/settings/server", headers=OPERATOR,
                         json={"desktop_broker_url": "172.30.7.20:8790"})
    assert refused.status_code == 400 and "desktop_broker_url" in refused.json()["detail"]
    # blank is a legitimate value: the feature switched off
    assert client.put("/api/settings/server", headers=OPERATOR,
                      json={"desktop_view_url": ""}).status_code == 200
    assert client.get("/api/status", headers=OPERATOR).json()["desktops"] is False
