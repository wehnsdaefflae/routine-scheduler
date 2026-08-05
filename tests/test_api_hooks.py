"""The webhook ingest route (the ONE unauthenticated API route) + trigger CRUD: URL-token
auth (constant-time, generic 404), payload cap, rate limit + spool cap, the durable
web→daemon handoff, and the routine-page CRUD with its 409/403 guards."""

import asyncio

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

    from conftest import FakeRunner

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


def test_create_report_trigger(api_client, make_routine):
    """The report trigger's web half: server-generated entry, no token/URL, the type's
    own generous default cooldown, and one-per-routine (409 on a second)."""
    c, tmp = api_client
    make_routine(slug="testr")
    r = c.post("/api/routines/testr/triggers", json={"type": "report"})
    assert r.status_code == 200
    trig = r.json()["trigger"]
    assert trig["type"] == "report"
    assert trig["cooldown_s"] == 900                  # the type's own default, not 60
    assert "token" not in trig and "url_path" not in trig
    raw = yaml.safe_load((tmp / "routines" / "testr" / "routine.yaml").read_text())
    assert raw["triggers"][0]["id"] == trig["id"]
    detail = c.get("/api/routines/testr").json()
    assert detail["triggers"][0]["type"] == "report"
    assert detail["triggers"][0]["url_path"] == ""
    # one inbox, one watcher
    assert c.post("/api/routines/testr/triggers",
                  json={"type": "report"}).status_code == 409
    # an explicit cooldown is honored
    assert c.delete(f"/api/routines/testr/triggers/{trig['id']}").status_code == 200
    r = c.post("/api/routines/testr/triggers", json={"type": "report", "cooldown_s": 300})
    assert r.json()["trigger"]["cooldown_s"] == 300


def test_patch_trigger_cooldown(api_client, make_routine):
    """Retuning is in-place: the webhook keeps its token (the URL a third party holds
    survives the edit), the daemon reads the new window, and only cooldown is settable."""
    c, tmp = api_client
    make_routine(slug="testr")
    trig = c.post("/api/routines/testr/triggers", json={"cooldown_s": 60}).json()["trigger"]

    r = c.patch(f"/api/routines/testr/triggers/{trig['id']}", json={"cooldown_s": 300})
    assert r.status_code == 200
    assert r.json()["trigger"]["cooldown_s"] == 300
    raw = yaml.safe_load((tmp / "routines" / "testr" / "routine.yaml").read_text())
    assert raw["triggers"][0]["cooldown_s"] == 300
    assert raw["triggers"][0]["token"] == trig["token"]        # identity survives the edit
    assert c.get("/api/routines/testr").json()["triggers"][0]["cooldown_s"] == 300
    assert TestClient(c.app).post(trig["url_path"], content=b"hi").status_code == 202

    assert c.patch(f"/api/routines/testr/triggers/{trig['id']}",
                   json={"cooldown_s": 0}).status_code == 200  # 0 = fire every event
    assert c.patch(f"/api/routines/testr/triggers/{trig['id']}",
                   json={"cooldown_s": -1}).status_code == 422
    assert c.patch(f"/api/routines/testr/triggers/{trig['id']}",
                   json={"cooldown_s": 60, "type": "report"}).status_code == 422
    assert c.patch("/api/routines/testr/triggers/t-ghost",
                   json={"cooldown_s": 60}).status_code == 404


def test_patch_trigger_guards(api_client, make_routine):
    """A cooldown edit is a config edit: same active-run guard and bearer gate as create."""
    c, tmp = api_client
    make_routine(slug="testr")
    trig = c.post("/api/routines/testr/triggers", json={"type": "report"}).json()["trigger"]
    path = f"/api/routines/testr/triggers/{trig['id']}"
    assert TestClient(c.app).patch(path, json={"cooldown_s": 60}).status_code == 401
    _mk_active_run(tmp, "testr")
    assert c.patch(path, json={"cooldown_s": 60}).status_code == 409


def _add_report_trigger(tmp, slug, *, tid="t-report01", cooldown_s=0, cap=24):
    path = tmp / "routines" / slug / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.setdefault("triggers", []).append(
        {"id": tid, "type": "report", "cooldown_s": cooldown_s, "max_fires_per_day": cap})
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _fire_once(c, tmp):
    from conftest import FakeRunner

    runner = FakeRunner()
    asyncio.run(TriggerManager(c.app.state.server, runner).tick(registry.scan(c.app.state.server)))
    return runner


def test_closure_message_does_not_buy_a_run(api_client, make_routine):
    """A closure asks nothing, so it must not WAKE the target — it rides in the inbox and
    is read by the next run that happens anyway. Anything else in the inbox still fires."""
    c, tmp = api_client
    make_routine(slug="testr")
    _add_report_trigger(tmp, "testr")
    inbox = tmp / "routines" / "testr" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    atomic_write_json(inbox / "msg-rep-R2.json", {
        "text": "REPORT R2 (answering R1 — closes the exchange, no reply needed)",
        "ts": "2026-08-05T23:00:00+02:00", "via": "report", "report": "R2",
        "from": "peer", "closes": True})
    assert _fire_once(c, tmp).fired == []                    # closure-only inbox stays quiet

    atomic_write_json(inbox / "msg-rep-R3.json", {
        "text": "REPORT R3 — real work", "ts": "2026-08-05T23:01:00+02:00",
        "via": "report", "report": "R3", "from": "peer"})
    assert _fire_once(c, tmp).fired == [("testr", "trigger")]             # a real report still wakes it
    # the closure was never consumed by the trigger — the fired run's drain owns that
    assert (inbox / "msg-rep-R2.json").exists()


def test_report_trigger_daily_cap(api_client, make_routine):
    """The cooldown bounds the RATE of fires; the cap bounds the day's TOTAL, so a
    routine-to-routine exchange cannot stay awake forever. Hitting it is visible."""
    c, tmp = api_client
    make_routine(slug="testr")
    _add_report_trigger(tmp, "testr", cooldown_s=0, cap=2)
    inbox = tmp / "routines" / "testr" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    atomic_write_json(inbox / "msg-1.json", {"text": "work", "ts": "2026-08-05T23:00:00+02:00"})

    assert _fire_once(c, tmp).fired == [("testr", "trigger")]
    assert _fire_once(c, tmp).fired == [("testr", "trigger")]
    assert _fire_once(c, tmp).fired == []                    # cap reached — no third fire
    events = (tmp / "routines" / ".control" / "health-events.jsonl").read_text(encoding="utf-8")
    assert "trigger_capped" in events
    assert events.count("trigger_capped") == 1               # once per trigger per day

    # a new day releases it (the counter is dated, not a rolling window)
    state = read_json(tmp / "routines" / ".control" / "triggers" / "testr" / "state.json")
    state["triggers"]["t-report01"]["day"] = "2026-08-04"
    atomic_write_json(tmp / "routines" / ".control" / "triggers" / "testr" / "state.json", state)
    assert _fire_once(c, tmp).fired == [("testr", "trigger")]


def test_patch_trigger_daily_cap(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    trig = c.post("/api/routines/testr/triggers", json={"type": "report"}).json()["trigger"]
    assert trig["max_fires_per_day"] == 24                   # the type's own default
    path = f"/api/routines/testr/triggers/{trig['id']}"
    assert c.patch(path, json={"max_fires_per_day": 6}).json()["trigger"]["max_fires_per_day"] == 6
    raw = yaml.safe_load((tmp / "routines" / "testr" / "routine.yaml").read_text())
    assert raw["triggers"][0]["max_fires_per_day"] == 6
    assert raw["triggers"][0]["cooldown_s"] == 900           # untouched by a partial patch
    assert c.patch(path, json={"max_fires_per_day": 0}).status_code == 200      # 0 = uncapped
    assert c.patch(path, json={"max_fires_per_day": -1}).status_code == 422
    assert c.patch(path, json={}).status_code == 400
    detail = c.get("/api/routines/testr").json()["triggers"][0]
    assert detail["max_fires_per_day"] == 0 and detail["fires_today"] == 0
