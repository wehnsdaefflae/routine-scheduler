"""Event triggers: the canonical config shape (validate_triggers), the durable event
spool, and the TriggerManager's coalesce/cooldown/fire pass. FakeRunner mirrors
tests/test_detached.py — on-disk fixtures, no subprocess, asyncio_mode=auto."""

import yaml

from rsched import triggers
from rsched.config import ServerConfig, load_routine
from rsched.daemon import registry
from rsched.daemon.triggers import TriggerManager, _event_text
from rsched.ids import now_iso
from rsched.paths import read_json


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    return s


class FakeRunner:
    def __init__(self):
        self.fired: list[tuple[str, str]] = []
        self.active: dict[str, str] = {}
        self.draining = False

    def is_active(self, slug: str) -> bool:
        return slug in self.active

    async def fire(self, cfg, *, reason="schedule") -> str:
        self.fired.append((cfg.slug, reason))
        self.active[cfg.slug] = "20260717-120000"
        return f"{cfg.slug}:20260717-120000"


WEBHOOK = {"id": "t-aaaa1111", "type": "webhook", "token": "tok-" + "a" * 28,
           "cooldown_s": 0}


def _routine(server, slug="webby", *, trig=None, enabled=True):
    d = server.routines_home / slug
    (d / "inbox").mkdir(parents=True, exist_ok=True)
    (d / "main.md").write_text("# main\n", encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": slug, "name": slug, "enabled": enabled,
        "description": "webhook test routine",
        "schedule": {"cron": "", "tz": "Europe/Berlin"},
        **({"triggers": trig} if trig is not None else {}),
    }), encoding="utf-8")
    return d


# -- config shape -----------------------------------------------------------------------


def test_validate_triggers_passthrough_and_defaults():
    entry = {"id": "t-1", "type": "webhook", "token": "s3cret", "note": "keep-me"}
    out, problems = triggers.validate_triggers([entry])
    assert problems == []
    assert out[0]["note"] == "keep-me"                       # per-type keys pass verbatim
    assert out[0]["cooldown_s"] == triggers.DEFAULT_COOLDOWN_S  # default filled in
    assert triggers.validate_triggers(None) == ([], [])


def test_validate_triggers_reports_and_drops_invalid():
    out, problems = triggers.validate_triggers("nope")
    assert out == [] and problems == ["triggers: expected a list"]
    raw = [
        "not-a-mapping",
        {"id": "t-1", "type": "carrier-pigeon"},
        {"type": "webhook", "token": "x"},                     # missing id
        {"id": "t-2", "type": "webhook"},                      # webhook without a token
        {"id": "t-3", "type": "webhook", "token": "x", "cooldown_s": -5},
        {"id": "t-3", "type": "webhook", "token": "y"},        # duplicate id
    ]
    out, problems = triggers.validate_triggers(raw)
    assert [t["id"] for t in out] == ["t-3"]
    assert out[0]["cooldown_s"] == triggers.DEFAULT_COOLDOWN_S  # junk value reset
    assert len(problems) == 6                       # one line per bad entry (incl. the reset)
    assert any("carrier-pigeon" in p for p in problems)
    assert any("missing id" in p for p in problems)
    assert any("without a token" in p for p in problems)
    assert any("duplicate id" in p for p in problems)


def test_validate_triggers_reserved_types_kept_but_flagged():
    raw = [{"id": "t-mail", "type": "imap", "host": "imap.example.org"},
           {"id": "t-drop", "type": "watch_path", "path": "~/drop"}]
    out, problems = triggers.validate_triggers(raw)
    assert [t["id"] for t in out] == ["t-mail", "t-drop"]      # same shape, kept verbatim
    assert out[0]["host"] == "imap.example.org"
    assert all("not implemented yet" in p for p in problems) and len(problems) == 2


def test_load_routine_canonicalizes_triggers(tmp_path):
    server = _server(tmp_path)
    d = _routine(server, trig=[dict(WEBHOOK), {"id": "t-bad", "type": "webhook"}])
    cfg, problems = load_routine(d)
    assert cfg is not None
    assert [t["id"] for t in cfg.triggers] == ["t-aaaa1111"]
    assert any("without a token" in p for p in problems)


# -- the spool --------------------------------------------------------------------------


def test_spool_roundtrip(tmp_path):
    server = _server(tmp_path)
    home = server.routines_home
    assert triggers.pending_events(home, "webby") == []
    assert triggers.slugs_with_events(home) == []
    p1 = triggers.write_event(home, "webby", trigger_id="t-1", payload="one",
                              content_type="text/plain", client="10.0.0.9")
    p2 = triggers.write_event(home, "webby", trigger_id="t-1", payload="two")
    assert triggers.pending_events(home, "webby") == sorted([p1, p2])
    assert triggers.slugs_with_events(home) == ["webby"]
    ev = read_json(p1)
    assert ev["payload"] == "one" and ev["trigger"] == "t-1" and ev["client"] == "10.0.0.9"
    triggers.write_state(home, "webby", {"last_fired": "x", "fires": 2})
    assert triggers.read_state(home, "webby")["fires"] == 2
    assert triggers.read_state(home, "elsewhere") == {}


def test_describe_triggers_rows(tmp_path):
    server = _server(tmp_path)
    home = server.routines_home
    triggers.write_event(home, "webby", trigger_id="t-aaaa1111", payload="x")
    triggers.write_state(home, "webby", {
        "last_fired": "2026-07-17T09:00:00+00:00", "fires": 1,
        "triggers": {"t-aaaa1111": {"last_fired": "2026-07-17T09:00:00+00:00", "events": 4}}})
    imap = {"id": "t-mail", "type": "imap", "cooldown_s": 60}
    rows = triggers.describe_triggers(home, "webby", [dict(WEBHOOK), imap])
    hook, mail = rows
    assert hook["url_path"] == f"/api/hooks/webby/{WEBHOOK['token']}"
    assert hook["last_fired"] == "2026-07-17T09:00:00+00:00"
    assert hook["events"] == 4 and hook["pending"] == 1
    assert mail["url_path"] == "" and mail["pending"] == 0
    assert triggers.describe_triggers(home, "webby", []) == []


# -- the manager ------------------------------------------------------------------------


async def test_tick_fires_once_and_injects_every_payload(tmp_path):
    server = _server(tmp_path)
    d = _routine(server, trig=[dict(WEBHOOK)])
    for n in range(3):
        triggers.write_event(server.routines_home, "webby",
                             trigger_id="t-aaaa1111", payload=f"payload-{n}")
    runner = FakeRunner()
    mgr = TriggerManager(server, runner)
    await mgr.tick(registry.scan(server))
    assert runner.fired == [("webby", "trigger")]              # N events → ONE fire
    msgs = sorted((d / "inbox").glob("msg-trig-*.json"))
    assert len(msgs) == 3                                      # …but every payload lands
    texts = [read_json(m)["text"] for m in msgs]
    assert all("[webhook event]" in t for t in texts)
    assert all(f"payload-{n}" in "".join(texts) for n in range(3))
    assert all(read_json(m)["via"] == "trigger" for m in msgs)
    assert triggers.pending_events(server.routines_home, "webby") == []
    state = triggers.read_state(server.routines_home, "webby")
    assert state["fires"] == 1 and state["triggers"]["t-aaaa1111"]["events"] == 3


async def test_tick_coalesces_while_active_and_draining(tmp_path):
    server = _server(tmp_path)
    d = _routine(server, trig=[dict(WEBHOOK)])
    triggers.write_event(server.routines_home, "webby", trigger_id="t-aaaa1111", payload="x")
    runner = FakeRunner()
    runner.active["webby"] = "20260717-110000"
    mgr = TriggerManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)
    assert runner.fired == []                                  # active run → event waits
    assert len(triggers.pending_events(server.routines_home, "webby")) == 1
    assert not list((d / "inbox").glob("msg-trig-*"))          # nothing injected early
    runner.active.clear()
    runner.draining = True
    await mgr.tick(catalog)
    assert runner.fired == []                                  # drain → still waits
    runner.draining = False
    await mgr.tick(catalog)
    assert runner.fired == [("webby", "trigger")]              # freed → the ONE fire


async def test_cooldown_defers_the_fire(tmp_path):
    server = _server(tmp_path)
    _routine(server, trig=[{**WEBHOOK, "cooldown_s": 3600}])
    triggers.write_state(server.routines_home, "webby", {"last_fired": now_iso(), "fires": 1})
    triggers.write_event(server.routines_home, "webby", trigger_id="t-aaaa1111", payload="x")
    runner = FakeRunner()
    mgr = TriggerManager(server, runner)
    await mgr.tick(registry.scan(server))
    assert runner.fired == []                                  # inside the window → waits
    assert len(triggers.pending_events(server.routines_home, "webby")) == 1
    # a stale stamp outside the window releases it
    triggers.write_state(server.routines_home, "webby",
                         {"last_fired": "2026-01-01T00:00:00+00:00", "fires": 1})
    await mgr.tick(registry.scan(server))
    assert runner.fired == [("webby", "trigger")]


async def test_stale_events_dropped(tmp_path):
    server = _server(tmp_path)
    d = _routine(server, trig=[dict(WEBHOOK)])
    triggers.write_event(server.routines_home, "webby", trigger_id="t-deleted", payload="x")
    runner = FakeRunner()
    mgr = TriggerManager(server, runner)
    await mgr.tick(registry.scan(server))
    assert runner.fired == []                                  # unknown trigger → dropped
    assert triggers.pending_events(server.routines_home, "webby") == []
    assert not list((d / "inbox").glob("msg-trig-*"))
    # a disabled routine's events are dropped too (the hook already 404s new ones)
    _routine(server, slug="off", trig=[dict(WEBHOOK)], enabled=False)
    triggers.write_event(server.routines_home, "off", trigger_id="t-aaaa1111", payload="x")
    await mgr.tick(registry.scan(server))
    assert runner.fired == []
    assert triggers.pending_events(server.routines_home, "off") == []


async def test_tick_never_raises(tmp_path):
    server = _server(tmp_path)
    triggers.write_event(server.routines_home, "ghost", trigger_id="t-1", payload="x")
    mgr = TriggerManager(server, FakeRunner())
    await mgr.tick({})                                         # no catalog entry → dropped, no raise


def test_event_text_shapes():
    full = _event_text({"trigger": "t-1", "ts": "2026-07-17T09:00:00+00:00",
                        "content_type": "application/json", "payload": '{"a": 1}'})
    assert full.startswith("[webhook event] trigger t-1 received 2026-07-17")
    assert "(application/json)" in full and full.endswith('{"a": 1}')
    empty = _event_text({"trigger": "t-1", "ts": "x", "payload": "  "})
    assert empty.endswith("(empty payload)")
