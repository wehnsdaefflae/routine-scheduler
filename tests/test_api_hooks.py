"""The webhook ingest route (the ONE unauthenticated API route) and the triggers it serves:
URL-token auth (constant-time, generic 404), payload cap, rate limit + spool cap, the durable
web→daemon handoff, and editing the trigger list through the routine page's one accept with
its validation and its 401/409 guards."""

import asyncio

import pytest
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


def test_hook_refuses_a_retired_routine_like_a_disabled_one(api_client, make_routine):
    """A routine whose finish line is reached may not fire (`RoutineInfo.fireable`), and the
    daemon drops its spooled events unread — so the hook must not answer 202 for one."""
    from rsched.engine import finishline

    c, tmp = api_client
    routine = make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    finishline.save(routine, {"outcomes": [{"text": "shipped", "judge": "you",
                                            "status": "met"}]}, now="2026-10-01T09:00:00")
    bare = TestClient(c.app)
    r = bare.post(f"/api/hooks/testr/{TOK}", content=b"x")
    assert r.status_code == 404
    assert r.json() == bare.post(f"/api/hooks/ghost/{TOK}", content=b"x").json()
    assert triggers.pending_events(tmp / "routines", "testr") == []


def test_hook_non_ascii_token_is_the_same_404(api_client, make_routine):
    """`secrets.compare_digest` refuses a str with non-ASCII characters by RAISING, so a URL
    token like `%C3%A9` turned the one unauthenticated route into a 500 — a traceback in the
    log per request, and a different answer than the 404 every other bad token gets."""
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    bare = TestClient(c.app)
    wrong = bare.post(f"/api/hooks/testr/{'x' * 32}", content=b"x")
    for slug in ("testr", "ghost"):           # with candidates, and on the equalised path
        r = bare.post(f"/api/hooks/{slug}/%C3%A9t%C3%A9", content=b"x")
        assert r.status_code == 404 and r.json() == wrong.json()
    assert triggers.pending_events(tmp / "routines", "testr") == []


def test_hook_walks_the_catalog_off_the_event_loop(api_client, make_routine, monkeypatch):
    """The ingest is async (it streams the body) and unauthenticated, and answering it walks
    every routine's config and run index. On the event loop that walk stalled every SSE stream
    and async route for as long as anyone cared to POST garbage at /api/hooks."""
    c, tmp = api_client
    make_routine(slug="testr")
    _add_trigger(tmp, "testr")
    real, on_loop = registry.scan, []

    def spy(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*args, **kwargs)

    monkeypatch.setattr(registry, "scan", spy)
    bare = TestClient(c.app)
    assert bare.post(f"/api/hooks/testr/{TOK}", content=b"x").status_code == 202
    assert bare.post(f"/api/hooks/ghost/{TOK}", content=b"x").status_code == 404
    assert on_loop == [False, False]


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


# -- editing: the routine page's one accept ------------------------------------------------
# The Triggers card is a field of the settings page (0.369.0): a trigger is added, re-bounded or
# removed in the draft and lands with the page's one accept (web/api_settings). What the server
# mints is IDENTITY — the id, and a webhook's token, the hook's only auth.

WEBHOOK = {"type": "webhook", "cooldown_s": 120, "max_fires_per_day": 0}
REPORT = {"type": "report", "cooldown_s": 900, "max_fires_per_day": 24}


def _accept(c, rows, slug="testr"):
    return c.post(f"/api/routines/{slug}/settings", json={"changes": {"triggers": rows}})


def _saved_triggers(tmp, slug="testr"):
    path = tmp / "routines" / slug / "routine.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("triggers") or []


def test_an_accepted_webhook_has_a_working_url_until_it_is_removed(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    assert _accept(c, [WEBHOOK]).status_code == 200
    [trig] = _saved_triggers(tmp)
    assert trig["type"] == "webhook" and trig["cooldown_s"] == 120
    assert len(trig["token"]) >= 24                      # server-generated, never client-supplied
    url = f"/api/hooks/testr/{trig['token']}"
    row = c.get("/api/routines/testr").json()["triggers"][0]     # the card's row
    assert row["url_path"] == url and row["last_fired"] == "" and row["pending"] == 0
    assert TestClient(c.app).post(url, content=b"hi").status_code == 202
    assert _accept(c, []).status_code == 200
    assert _saved_triggers(tmp) == []
    assert TestClient(c.app).post(url, content=b"hi").status_code == 404


def test_a_zero_bound_is_a_value_not_an_absence(api_client, make_routine):
    """`0` is documented as no wait (cooldown) and no cap (daily fires). The accept read a
    zero cooldown as falsy and minted the type's default instead — 60 s, or 900 s for a
    report trigger — so the value the operator typed never reached the file."""
    c, tmp = api_client
    make_routine(slug="testr")
    r = _accept(c, [{**WEBHOOK, "cooldown_s": 0},
                    {**REPORT, "cooldown_s": 0, "max_fires_per_day": 0}])
    assert r.status_code == 200, r.text
    assert sorted((t["type"], t["cooldown_s"], t["max_fires_per_day"])
                  for t in _saved_triggers(tmp)) == [("report", 0, 0), ("webhook", 0, 0)]


def test_a_report_trigger_has_no_url_and_a_routine_has_one(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    assert _accept(c, [REPORT]).status_code == 200
    [trig] = _saved_triggers(tmp)
    assert trig["type"] == "report" and "token" not in trig
    assert c.get("/api/routines/testr").json()["triggers"][0]["url_path"] == ""
    # one inbox, one watcher — the card disables its button, the accept refuses the second
    r = _accept(c, [REPORT, {**REPORT, "cooldown_s": 300}])
    assert r.status_code == 422 and "one report trigger" in r.json()["detail"]
    assert _saved_triggers(tmp) == [trig]


@pytest.mark.parametrize("row", [
    {**WEBHOOK, "type": "imap"},             # reserved shape: nothing would ever fire it
    {**WEBHOOK, "type": "nonsense"},
    {**WEBHOOK, "cooldown_s": -1},
    {**WEBHOOK, "max_fires_per_day": "lots"},
    {**WEBHOOK, "cooldown_s": True},
    {**WEBHOOK, "host": "imap.example.org"},  # a key no creatable trigger reads
])
def test_the_accept_refuses_a_trigger_it_cannot_create(api_client, make_routine, row):
    """Any type that was not `report` used to become a WEBHOOK — a new token, a new public
    entry point — and a bound the loader would later replace with its default was written
    as given."""
    c, tmp = api_client
    make_routine(slug="testr")
    r = _accept(c, [row])
    assert r.status_code == 422, r.text
    assert _saved_triggers(tmp) == []


def test_changing_a_webhooks_bounds_mints_a_new_url(api_client, make_routine):
    """A trigger's id and token are identity, not configuration, so the accept matches a row
    to a trigger by what it configures: an unchanged row keeps its URL (test_patterns), a
    row whose bounds changed stands for a NEW trigger — the card says so before the accept."""
    c, tmp = api_client
    make_routine(slug="testr")
    _accept(c, [WEBHOOK])
    old = _saved_triggers(tmp)[0]["token"]
    assert _accept(c, [{**WEBHOOK, "cooldown_s": 300}]).status_code == 200
    [trig] = _saved_triggers(tmp)
    assert trig["cooldown_s"] == 300 and trig["token"] != old
    bare = TestClient(c.app)
    assert bare.post(f"/api/hooks/testr/{old}", content=b"x").status_code == 404
    assert bare.post(f"/api/hooks/testr/{trig['token']}", content=b"x").status_code == 202


def test_trigger_edits_are_bearer_gated_and_wait_for_an_active_run(api_client, make_routine):
    c, tmp = api_client
    make_routine(slug="testr")
    assert TestClient(c.app).post("/api/routines/testr/settings",
                                  json={"changes": {"triggers": [WEBHOOK]}}).status_code == 401
    _mk_active_run(tmp, "testr")
    assert _accept(c, [WEBHOOK]).status_code == 409
    assert _saved_triggers(tmp) == []


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
