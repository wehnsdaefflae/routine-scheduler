"""Web Push: VAPID key persistence, the subscription store, and the decision sender's
diff-and-dedupe behavior. Actual webpush sends are mocked — no network."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from rsched.config import ServerConfig
from rsched.daemon.events import EventBus
from rsched.engine import inbox
from rsched.web import push


@pytest.fixture
def client(api_client, make_routine):
    make_routine(slug="apir")
    return api_client


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


def test_concurrent_first_use_yields_one_key(tmp_path):
    """Two first requests at once each generated a pair and the later save replaced the
    earlier, so one browser held a public key whose private half was gone. Every caller now
    gets the same key, and the private key is not world-readable."""
    from conftest import hammer

    server = _server(tmp_path)
    keys: list[str] = []
    assert hammer(lambda _tag: keys.append(push.vapid_public_key(server))) == []
    assert len(set(keys)) == 1 and keys[0] == push.vapid_public_key(server)
    assert (tmp_path / "vapid-private.pem").stat().st_mode & 0o777 == 0o600


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
    monkeypatch.setattr(push, "_send_one", lambda _srv, sub, payload: sent.append(payload) or True)

    assert push.notify_new_decisions(server) == 1
    assert sent[0]["tag"] == "rsched-q-1" and "Ship it?" in sent[0]["body"]
    assert sent[0]["url"] == "/#/questions" and "asker" in sent[0]["title"]
    # the same decision never pushes twice…
    assert push.notify_new_decisions(server) == 0
    # …but a new one does
    inbox.file_question(d, "q-2", "And now?", [], "20260712-080000")
    assert push.notify_new_decisions(server) == 1
    assert sent[-1]["tag"] == "rsched-q-2"


def test_answered_decision_is_withdrawn(make_routine, tmp_path, monkeypatch):
    """msg-4 (2026-08-31): once a decision is answered its phone notification must be retracted.
    The sender pushes a same-tag `close` payload (sw.js clears the tray notification) and forgets
    the qid so a decision that re-opens later alerts afresh."""
    import json as _json

    server = _server(tmp_path)
    d = make_routine(slug="asker")
    inbox.file_question(d, "q-1", "Ship it?", ["yes", "no"], "20260712-070000")
    push.add_subscription(server, SUB_A)
    sent: list[dict] = []
    monkeypatch.setattr(push, "_send_one", lambda _srv, sub, payload: sent.append(payload) or True)

    assert push.notify_new_decisions(server) == 1        # the ask goes out
    assert not sent[-1].get("close")

    # the operator answers it — an inbox answer file is the durable answered marker
    (d / "inbox").mkdir(parents=True, exist_ok=True)
    (d / "inbox" / "answer-q-1.json").write_text(_json.dumps({"text": "yes"}))

    assert push.notify_new_decisions(server) == 1        # exactly one withdrawal push
    assert sent[-1]["close"] is True and sent[-1]["tag"] == "rsched-q-1"

    # idempotent: the qid was forgotten, so a further pass sends nothing
    before = len(sent)
    assert push.notify_new_decisions(server) == 0
    assert len(sent) == before


def test_a_standing_proposal_is_pushed_and_withdrawn_once_decided(tmp_path, monkeypatch):
    """A queued proposal (a creation, a met goal, library drift) waits on a person exactly as
    a question does. The sender counted none of them, so a scheduled run's proposal reached
    nobody away from the console; it is pushed once, keyed by its `pc-` id, and retracted
    when the operator decides it (the record leaves the queue)."""
    from rsched import pending

    server = _server(tmp_path)
    rec = pending.queue(server.routines_home, kind="create_routine", routine="scout",
                        run_id="scout:20260712-070000", fields={"slug": "new-one"},
                        summary="create routine new-one")
    push.add_subscription(server, SUB_A)
    sent: list[dict] = []
    monkeypatch.setattr(push, "_send_one", lambda _srv, sub, payload: sent.append(payload) or True)

    assert push.notify_new_decisions(server) == 1
    assert sent[-1]["tag"] == f"rsched-{rec['id']}" and "scout" in sent[-1]["title"]
    assert "create routine new-one" in sent[-1]["body"]
    assert push.notify_new_decisions(server) == 0          # never twice
    assert pending.drop(server.routines_home, rec["id"])
    assert push.notify_new_decisions(server) == 1
    assert sent[-1] == {"tag": f"rsched-{rec['id']}", "close": True}


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


def test_send_asks_the_push_service_to_hold_the_message(tmp_path, monkeypatch):
    """pywebpush defaults ttl=0 — deliver to a currently-connected device or DISCARD. That
    silently throws away exactly the notification an away operator needs (phone asleep), and
    the send still looks successful. Every send must carry a real TTL."""
    server = _server(tmp_path)
    push.vapid_public_key(server)
    push.add_subscription(server, SUB_A)
    seen: dict = {}

    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: seen.update(kw))
    assert push.send_to_all(server, {"title": "t"}) == 1
    assert seen["ttl"] >= 3600, seen.get("ttl")


def test_push_api_routes(client):
    c, _tmp = client
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
    # the web app manifest is served from the root (PWA scope = whole console) with the
    # correct media type and display:standalone — the prerequisite for Web Push on iOS.
    mani = c.get("/manifest.webmanifest")
    assert mani.status_code == 200 and "manifest+json" in mani.headers["content-type"]
    assert mani.json()["display"] == "standalone"
    # the served console links the manifest + apple install tags, and its icon resolves
    home = c.get("/").text
    assert "/manifest.webmanifest" in home and "apple-mobile-web-app-capable" in home
    assert c.get("/static/icon.svg").status_code == 200


def test_the_installed_console_wears_the_console_ground():
    """An installed PWA takes its title bar and launch splash from the MANIFEST, a browser tab
    from index.html's theme-color — and the manifest still carried the pre-watchfloor ground
    (#0f1419), so the installed console opened on a colour the console itself never paints.
    Both must be the dark half of base.css's `--deck`, the colour `body` is painted with."""
    import json
    import re

    static = Path(__file__).resolve().parent.parent / "static"
    deck = re.search(r"--deck:\s*light-dark\(#[0-9a-f]{6},\s*(#[0-9a-f]{6})\)",
                     (static / "base.css").read_text(encoding="utf-8"))
    assert deck, "base.css no longer declares --deck as light-dark(light, dark)"
    manifest = json.loads((static / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["theme_color"] == manifest["background_color"] == deck.group(1)
    html = (static / "index.html").read_text(encoding="utf-8")
    assert f'<meta name="theme-color" content="{deck.group(1)}">' in html
    # Chrome deprecates the apple-only spelling and asks for the standard one beside it; the
    # apple one stays for iOS, whose status-bar style depends on it
    assert '<meta name="mobile-web-app-capable" content="yes">' in html


def test_worker_auth_cache_literals_stay_paired():
    """sw.js is a CLASSIC worker (no bundler, no import) and cannot read localStorage, so the
    Cache API name/key carrying the operator token to it is spelled out in both files. Drift
    there kills subscription healing SILENTLY — the worker finds no credential, returns, and
    the only symptom is notifications that stop arriving weeks later."""
    static = Path(__file__).resolve().parent.parent / "static"
    api_js = (static / "api.js").read_text(encoding="utf-8")
    sw_js = (static / "sw.js").read_text(encoding="utf-8")
    for literal in ('"rsched-auth"', '"/__auth_token"'):
        assert literal in api_js, literal
        assert literal in sw_js, literal


def test_a_busy_bus_cannot_hold_a_push_back(monkeypatch):
    """Every LLM call of every live run is a bus event, so a busy fleet never falls silent
    for the debounce window — and the listener waited for that silence before every diff, so
    a decision's push waited for the whole fleet to go quiet. The burst is capped now: the
    diff runs while the chatter goes on."""
    monkeypatch.setattr(push, "_QUIET_S", 0.2, raising=False)
    monkeypatch.setattr(push, "_MAX_COALESCE_S", 0.5, raising=False)
    diffs: list[int] = []
    monkeypatch.setattr(push, "notify_new_decisions", lambda _server: diffs.append(1) or 0)

    async def chatter() -> int:
        bus = EventBus()
        listener = asyncio.create_task(push.bus_listener(object(), bus))
        await asyncio.sleep(0)                    # let it subscribe
        for _ in range(25):                       # 2.5 s, one event every 0.1 s
            bus.publish({"event": "llm_task"})
            await asyncio.sleep(0.1)
        during = len(diffs)
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
        return during

    assert asyncio.run(chatter()) >= 2
