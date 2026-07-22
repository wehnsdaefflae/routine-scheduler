"""The webhook ingest route (the ONE unauthenticated API route) + trigger CRUD: URL-token
auth (constant-time, generic 404), payload cap, rate limit + spool cap, the durable
web→daemon handoff, and the routine-page CRUD with its 409/403 guards."""

import yaml
from fastapi.testclient import TestClient

from rsched import registry, triggers
from rsched.daemon.triggers import TriggerManager
from rsched.paths import atomic_write_json, read_json

TOK = "tok-" + "a" * 28


def _add_trigger(tmp, slug, *, tid="t-11112222", token=TOK, cooldown_s=60):
    path = tmp / "routines" / slug / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.setdefault("triggers", []).append(
        {"id": tid, "type": "webhook", "token": token, "cooldown_s": cooldown_s})
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _mk_active_run(tmp, slug, ts="20260717-090000"):
    run_dir = tmp / "routines" / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": "running", "pid": 4242})


# -- ingest -------------------------------------------------------------------------------


def test_hook_accepts_without_bearer_and_never_echoes(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    bare = TestClient(c.app)   # NO Authorization header — the URL token is the auth
    r = bare.post(f"/api/hooks/testr/{TOK}", content=b'{"event": "push"}',
                  headers={"content-type": "application/json"})
    assert r.status_code == 202
    assert r.json() == {"ok": True}                     # the payload is NEVER echoed back
    events = triggers.pending_events(tmp / "routines", "testr")
    assert len(events) == 1
    ev = read_json(events[0])
    assert ev["trigger"] == "t-11112222"
    assert ev["payload"] == '{"event": "push"}'
    assert ev["content_type"] == "application/json"


def test_hook_generic_404_for_slug_token_and_disabled(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    bare = TestClient(c.app)
    wrong_token = bare.post(f"/api/hooks/testr/{'x' * 32}", content=b"x")
    unknown_slug = bare.post(f"/api/hooks/ghost/{TOK}", content=b"x")
    assert wrong_token.status_code == unknown_slug.status_code == 404
    # one indistinguishable answer — no existence oracle
    assert wrong_token.json() == unknown_slug.json()
    path = tmp / "routines" / "testr" / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["enabled"] = False
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    disabled = bare.post(f"/api/hooks/testr/{TOK}", content=b"x")
    assert disabled.status_code == 404 and disabled.json() == wrong_token.json()
    assert triggers.pending_events(tmp / "routines", "testr") == []


def test_hook_payload_size_cap(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    bare = TestClient(c.app)
    r = bare.post(f"/api/hooks/testr/{TOK}",
                  content=b"x" * (triggers.MAX_PAYLOAD_BYTES + 1))
    assert r.status_code == 413
    assert triggers.pending_events(tmp / "routines", "testr") == []
    ok = bare.post(f"/api/hooks/testr/{TOK}", content=b"x" * 512)
    assert ok.status_code == 202


def test_hook_streaming_body_cap_without_content_length(api_client, make_routine):
    """A chunked body (iterator content → no Content-Length) can't sneak past the cap:
    the declared-length pre-check is skipped, so the streaming reader must abort it."""
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    bare = TestClient(c.app)

    def _huge():
        for _ in range(triggers.MAX_PAYLOAD_BYTES // 1024 + 2):
            yield b"x" * 1024

    r = bare.post(f"/api/hooks/testr/{TOK}", content=_huge())
    assert r.status_code == 413
    assert triggers.pending_events(tmp / "routines", "testr") == []


def test_hook_rate_limit(api_client, make_routine, monkeypatch):
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    monkeypatch.setattr("rsched.web.api_hooks.RATE_MAX_ACCEPTS", 2)
    bare = TestClient(c.app)
    assert bare.post(f"/api/hooks/testr/{TOK}", content=b"1").status_code == 202
    assert bare.post(f"/api/hooks/testr/{TOK}", content=b"2").status_code == 202
    r = bare.post(f"/api/hooks/testr/{TOK}", content=b"3")
    assert r.status_code == 429
    assert len(triggers.pending_events(tmp / "routines", "testr")) == 2


def test_hook_spool_cap(api_client, make_routine, monkeypatch):
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    monkeypatch.setattr("rsched.triggers.MAX_PENDING_EVENTS", 1)
    bare = TestClient(c.app)
    assert bare.post(f"/api/hooks/testr/{TOK}", content=b"1").status_code == 202
    assert bare.post(f"/api/hooks/testr/{TOK}", content=b"2").status_code == 429
    assert len(triggers.pending_events(tmp / "routines", "testr")) == 1


def test_hook_to_daemon_handoff(api_client, make_routine):
    """End to end across the ownership seam: the web route only spools; the daemon-side
    manager turns the spooled event into inbox messages + a fire."""
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr", cooldown_s=0)
    bare = TestClient(c.app)
    for n in range(2):
        assert bare.post(f"/api/hooks/testr/{TOK}", content=f"evt-{n}".encode()).status_code == 202

    class FakeRunner:
        def __init__(self):
            self.fired = []
            self.draining = False

        def is_active(self, slug):
            return False

        async def fire(self, cfg, *, reason="schedule"):
            self.fired.append((cfg.slug, reason))
            return f"{cfg.slug}:20260717-120000"

    server = c.app.state.server
    runner = FakeRunner()
    import asyncio

    asyncio.run(TriggerManager(server, runner).tick(registry.scan(server)))
    assert runner.fired == [("testr", "trigger")]
    msgs = sorted((tmp / "routines" / "testr" / "inbox").glob("msg-trig-*.json"))
    assert len(msgs) == 2
    texts = "".join(read_json(m)["text"] for m in msgs)
    assert "evt-0" in texts and "evt-1" in texts
    assert triggers.pending_events(tmp / "routines", "testr") == []


# -- CRUD ---------------------------------------------------------------------------------


def test_create_and_delete_trigger(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    r = c.post("/api/routines/testr/triggers", json={"cooldown_s": 120})
    assert r.status_code == 200
    trig = r.json()["trigger"]
    assert trig["type"] == "webhook" and trig["cooldown_s"] == 120
    assert trig["url_path"] == f"/api/hooks/testr/{trig['token']}"
    assert len(trig["token"]) >= 24                      # server-generated, never client-supplied
    raw = yaml.safe_load((tmp / "routines" / "testr" / "routine.yaml").read_text())
    assert raw["triggers"][0]["id"] == trig["id"]
    # the detail payload renders the card's rows
    detail = c.get("/api/routines/testr").json()
    assert detail["triggers"][0]["url_path"] == trig["url_path"]
    assert detail["triggers"][0]["last_fired"] == "" and detail["triggers"][0]["pending"] == 0
    # the fresh hook works immediately
    assert TestClient(c.app).post(trig["url_path"], content=b"hi").status_code == 202
    # delete: the URL stops matching, the config entry is gone
    assert c.delete(f"/api/routines/testr/triggers/{trig['id']}").status_code == 200
    raw = yaml.safe_load((tmp / "routines" / "testr" / "routine.yaml").read_text())
    assert raw["triggers"] == []
    assert TestClient(c.app).post(f"/api/hooks/testr/{trig['token']}",
                                  content=b"hi").status_code == 404
    assert c.delete("/api/routines/testr/triggers/t-ghost").status_code == 404


def test_trigger_crud_guards(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    make_routine(slug="clarification")   # the protected wizard template
    _mk_active_run(tmp, "testr")
    assert c.post("/api/routines/testr/triggers", json={}).status_code == 409
    assert c.post("/api/routines/clarification/triggers", json={}).status_code == 403
    assert c.post("/api/routines/ghost/triggers", json={}).status_code == 404
    # CRUD stays bearer-gated (only the hook ingest is public)
    bare = TestClient(c.app)
    assert bare.post("/api/routines/testr/triggers", json={}).status_code == 401
