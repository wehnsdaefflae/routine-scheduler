"""Web Push: VAPID key persistence, the subscription store, and the decision sender's
diff-and-dedupe behavior. Actual webpush sends are mocked — no network."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched.config import ServerConfig, load_server_config
from rsched.engine import inbox
from rsched.web import push
from rsched.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(tmp_path / "library"),
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c, tmp_path


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.source = tmp_path / "config.yaml"          # push state lands next to the config
    s.routines_home = tmp_path / "routines"
    s.libraries_home = tmp_path / "library"
    return s


SUB_A = {"endpoint": "https://push.example/a", "keys": {"p256dh": "x", "auth": "y"}}
SUB_B = {"endpoint": "https://push.example/b", "keys": {"p256dh": "x", "auth": "y"}}


def test_vapid_key_generated_once_and_stable(tmp_path):
    server = _server(tmp_path)
    key1 = push.vapid_public_key(server)
    key2 = push.vapid_public_key(server)
    assert key1 == key2 and len(key1) > 40 and "=" not in key1   # urlsafe, unpadded
    assert (tmp_path / "vapid-private.pem").exists()


def test_subscription_store_upserts_by_endpoint(tmp_path):
    server = _server(tmp_path)
    assert push.subscriptions(server) == []
    assert push.add_subscription(server, SUB_A) == 1
    assert push.add_subscription(server, SUB_B) == 2
    assert push.add_subscription(server, {**SUB_A, "keys": {"p256dh": "new", "auth": "n"}}) == 2
    subs = push.subscriptions(server)
    assert {s["endpoint"] for s in subs} == {SUB_A["endpoint"], SUB_B["endpoint"]}
    assert push.remove_subscription(server, SUB_A["endpoint"]) == 1
    assert push.remove_subscription(server, "https://push.example/ghost") == 1


def test_notify_pushes_each_new_decision_once(make_routine, tmp_path, monkeypatch):
    server = _server(tmp_path)
    d = make_routine(slug="asker")
    inbox.file_question(d, "q-1", "Ship it?", ["yes", "no"], "20260712-070000")
    push.add_subscription(server, SUB_A)
    sent: list[dict] = []
    monkeypatch.setattr(push, "_send_one", lambda srv, sub, payload: sent.append(payload) or True)

    assert push.notify_new_decisions(server) == 1
    assert sent[0]["tag"] == "rsched-q-1" and "Ship it?" in sent[0]["body"]
    assert sent[0]["url"] == "/#/questions" and "asker" in sent[0]["title"]
    # the same decision never pushes twice…
    assert push.notify_new_decisions(server) == 0
    # …but a new one does
    inbox.file_question(d, "q-2", "And now?", [], "20260712-080000")
    assert push.notify_new_decisions(server) == 1
    assert sent[-1]["tag"] == "rsched-q-2"


def test_notify_is_a_noop_without_subscribers(make_routine, tmp_path, monkeypatch):
    server = _server(tmp_path)
    d = make_routine(slug="quiet")
    inbox.file_question(d, "q-9", "Anyone?", [], "20260712-070000")
    called = []
    monkeypatch.setattr(push, "_send_one", lambda *a: called.append(a) or True)
    assert push.notify_new_decisions(server) == 0
    assert called == []
    # and the dedupe memory was NOT burned — subscribing later still pushes the open one
    push.add_subscription(server, SUB_A)
    assert push.notify_new_decisions(server) == 1


def test_dead_subscription_is_dropped(tmp_path, monkeypatch):
    from pywebpush import WebPushException

    server = _server(tmp_path)
    push.vapid_public_key(server)                # ensure a key exists for the send path
    push.add_subscription(server, SUB_A)

    class _Resp:
        status_code = 410

    def gone(**kwargs):
        raise WebPushException("gone", response=_Resp())

    monkeypatch.setattr(push, "webpush", None, raising=False)
    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", gone)
    assert push.send_to_all(server, {"title": "t"}) == 0
    assert push.subscriptions(server) == []      # 410 → removed on the spot


def test_push_api_routes(client):
    c, tmp = client
    info = c.get("/api/push").json()
    assert info["subscriptions"] == 0 and len(info["public_key"]) > 40
    r = c.post("/api/push/subscribe", json={"subscription": SUB_A})
    assert r.json() == {"ok": True, "subscriptions": 1}
    assert c.post("/api/push/subscribe", json={"subscription": {}}).status_code == 400
    r = c.post("/api/push/unsubscribe", json={"endpoint": SUB_A["endpoint"]})
    assert r.json() == {"ok": True, "subscriptions": 0}
    # the service worker is served from the root so its scope covers the console
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert "notificationclick" in sw.text
