"""One-shot time trigger (schedule_run): parse_fire_at, the request spool, the OneShotManager
fire/consume/defer/drop passes, the schedule_run action gate + engine handler, and the web
endpoints. FakeRunner mirrors tests/test_triggers.py — on-disk fixtures, asyncio_mode=auto."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml

from rsched import schedule_once
from rsched.config import ServerConfig
from rsched.daemon import registry
from rsched.daemon.schedule_once import OneShotManager
from rsched.engine.actions import validate_action
from rsched.engine.interact import handle_schedule_run
from rsched.grants import GrantPolicy
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


def _routine(server, slug="oneshot", *, enabled=True):
    d = server.routines_home / slug
    (d / "inbox").mkdir(parents=True, exist_ok=True)
    (d / "main.md").write_text("# main\n", encoding="utf-8")
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": slug, "name": slug, "enabled": enabled,
        "description": "one-shot test routine",
        "schedule": {"cron": "", "tz": "Europe/Berlin"},
    }), encoding="utf-8")
    return d


def _loop(server, *, run_id="run:1"):
    ctx = SimpleNamespace(server=SimpleNamespace(routines_home=server.routines_home),
                          run_id=run_id)
    return SimpleNamespace(ctx=ctx)


# -- parse_fire_at ----------------------------------------------------------------------


def test_parse_fire_at_relative_and_absolute():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert schedule_once.parse_fire_at("+3d", now) == now + timedelta(days=3)
    assert schedule_once.parse_fire_at("+2h", now) == now + timedelta(hours=2)
    assert schedule_once.parse_fire_at("+30m", now) == now + timedelta(minutes=30)
    assert schedule_once.parse_fire_at("+45s", now) == now + timedelta(seconds=45)
    assert schedule_once.parse_fire_at("2026-06-01T03:00:00+00:00", now) == \
        datetime(2026, 6, 1, 3, tzinfo=UTC)
    naive = schedule_once.parse_fire_at("2026-06-01T03:00:00", now)   # naive read as UTC
    assert naive == datetime(2026, 6, 1, 3, tzinfo=UTC) and naive.tzinfo == UTC


def test_parse_fire_at_rejects_past_bad_and_far():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for bad in ["", "yesterday", "+3x", "2020-01-01T00:00:00+00:00", "+400d"]:
        with pytest.raises(ValueError):
            schedule_once.parse_fire_at(bad, now)


# -- the request spool ------------------------------------------------------------------


def test_spool_roundtrip_and_cancel(tmp_path):
    server = _server(tmp_path)
    home = server.routines_home
    assert schedule_once.pending_requests(home, "oneshot") == []
    assert schedule_once.slugs_with_requests(home) == []
    r1 = schedule_once.arm(home, "oneshot", fire_at=datetime(2026, 6, 1, tzinfo=UTC),
                           reason="a", requested_by="ui")
    schedule_once.arm(home, "oneshot", fire_at=datetime(2026, 6, 2, tzinfo=UTC),
                      reason="b", requested_by="x:1")
    assert r1["id"].startswith("so-") and r1["active"] is True
    assert len(schedule_once.pending_requests(home, "oneshot")) == 2
    assert schedule_once.slugs_with_requests(home) == ["oneshot"]
    desc = schedule_once.describe(home, "oneshot")
    assert len(desc["armed"]) == 2 and desc["fires"] == 0
    assert desc["armed"][0]["fire_at"] <= desc["armed"][1]["fire_at"]   # sorted by fire_at
    assert schedule_once.cancel(home, "oneshot", r1["id"]) == 1
    assert schedule_once.cancel(home, "oneshot", "so-nope") == 0        # idempotent miss
    assert len(schedule_once.pending_requests(home, "oneshot")) == 1
    assert schedule_once.cancel(home, "oneshot") == 1                   # cancel ALL remaining
    assert schedule_once.pending_requests(home, "oneshot") == []


# -- the manager ------------------------------------------------------------------------


async def test_tick_fires_due_injects_reason_and_consumes(tmp_path):
    server = _server(tmp_path)
    d = _routine(server)
    past = datetime.now(UTC) - timedelta(minutes=1)
    schedule_once.arm(server.routines_home, "oneshot", fire_at=past,
                      reason="wake up and re-check", requested_by="self:1")
    runner = FakeRunner()
    await OneShotManager(server, runner).tick(registry.scan(server))
    assert runner.fired == [("oneshot", "schedule_once")]
    msgs = list((d / "inbox").glob("msg-once-*.json"))
    assert len(msgs) == 1
    msg = read_json(msgs[0])
    assert "wake up and re-check" in msg["text"] and msg["via"] == "schedule_once"
    # auto-deactivate = consume: the armed file is gone; the ledger records the fire
    assert schedule_once.pending_requests(server.routines_home, "oneshot") == []
    state = schedule_once.read_state(server.routines_home, "oneshot")
    assert state["fires"] == 1 and state["last_fired"]


async def test_tick_leaves_a_not_yet_due_request(tmp_path):
    server = _server(tmp_path)
    _routine(server)
    future = datetime.now(UTC) + timedelta(hours=1)
    schedule_once.arm(server.routines_home, "oneshot", fire_at=future,
                      reason="later", requested_by="x")
    runner = FakeRunner()
    await OneShotManager(server, runner).tick(registry.scan(server))
    assert runner.fired == []
    assert len(schedule_once.pending_requests(server.routines_home, "oneshot")) == 1


async def test_tick_defers_while_active_and_draining_then_fires(tmp_path):
    server = _server(tmp_path)
    _routine(server)
    past = datetime.now(UTC) - timedelta(minutes=1)
    schedule_once.arm(server.routines_home, "oneshot", fire_at=past, reason="x",
                      requested_by="x")
    runner = FakeRunner()
    runner.active["oneshot"] = "20260717-110000"
    mgr = OneShotManager(server, runner)
    catalog = registry.scan(server)
    await mgr.tick(catalog)
    assert runner.fired == [] and len(schedule_once.pending_requests(
        server.routines_home, "oneshot")) == 1                 # active → waits, nothing injected
    runner.active.clear()
    runner.draining = True
    await mgr.tick(catalog)
    assert runner.fired == []                                  # drain → still waits
    runner.draining = False
    await mgr.tick(catalog)
    assert runner.fired == [("oneshot", "schedule_once")]      # freed → the ONE fire


async def test_tick_drops_request_for_disabled_routine(tmp_path):
    server = _server(tmp_path)
    _routine(server, enabled=False)
    past = datetime.now(UTC) - timedelta(minutes=1)
    schedule_once.arm(server.routines_home, "oneshot", fire_at=past, reason="x",
                      requested_by="x")
    runner = FakeRunner()
    await OneShotManager(server, runner).tick(registry.scan(server))
    assert runner.fired == []
    assert schedule_once.pending_requests(server.routines_home, "oneshot") == []   # dropped


async def test_tick_drops_expired_request(tmp_path):
    server = _server(tmp_path)
    _routine(server)
    now = datetime.now(UTC)
    schedule_once.arm(server.routines_home, "oneshot", fire_at=now - timedelta(minutes=5),
                      reason="x", requested_by="x", expires_at=now - timedelta(minutes=1))
    runner = FakeRunner()
    await OneShotManager(server, runner).tick(registry.scan(server))
    assert runner.fired == []
    assert schedule_once.pending_requests(server.routines_home, "oneshot") == []   # dropped


# -- the schedule_run action: schema + capability gate ----------------------------------


def test_validate_action_schedule_run():
    ok = {"say": "s", "kind": "schedule_run", "target": "r", "fire_at": "+3d", "reason": "go"}
    assert validate_action(ok) == []
    # cancel needs no fire_at / reason
    assert validate_action({"say": "s", "kind": "schedule_run", "target": "r",
                            "cancel": True}) == []
    assert validate_action({"say": "s", "kind": "schedule_run", "target": "r",
                            "fire_at": "+3d"})                        # missing reason
    assert validate_action({"say": "s", "kind": "schedule_run", "target": "Not A Slug",
                            "fire_at": "+3d", "reason": "g"})         # target not a slug
    assert validate_action({"say": "s", "kind": "schedule_run", "fire_at": "+3d",
                            "reason": "g"})                          # missing target


def test_schedule_run_capability_gate():
    obj = {"say": "s", "kind": "schedule_run", "target": "r", "fire_at": "+3d", "reason": "g"}
    assert validate_action(obj, grants=GrantPolicy(actions=frozenset({"schedule_run"}))) == []
    denial = validate_action(obj, grants=GrantPolicy(actions=frozenset()))
    assert denial and any("scheduling" in p.lower() for p in denial)


# -- the engine handler -----------------------------------------------------------------


def test_handle_schedule_run_arms_then_cancels(tmp_path):
    server = _server(tmp_path)
    _routine(server, slug="target-r")
    loop = _loop(server, run_id="self-audit:20260719-103133")
    obs = handle_schedule_run(loop, {"target": "target-r", "fire_at": "+3d",
                                     "reason": "re-check"}, 0.0)
    assert obs["armed"].startswith("so-")
    armed = schedule_once.pending_requests(server.routines_home, "target-r")
    assert len(armed) == 1
    rec = schedule_once.read_request(armed[0])
    assert rec["requested_by"] == "self-audit:20260719-103133" and rec["reason"] == "re-check"
    obs2 = handle_schedule_run(loop, {"target": "target-r", "cancel": True,
                                      "id": obs["armed"]}, 0.0)
    assert obs2["cancelled"] == 1
    assert schedule_once.pending_requests(server.routines_home, "target-r") == []


def test_handle_schedule_run_unknown_target_and_bad_fire_at(tmp_path):
    server = _server(tmp_path)
    _routine(server, slug="target-r")
    loop = _loop(server)
    assert handle_schedule_run(loop, {"target": "ghost", "fire_at": "+3d",
                                      "reason": "g"}, 0.0)["unknown_target"]
    bad = handle_schedule_run(loop, {"target": "target-r", "fire_at": "yesterday",
                                     "reason": "g"}, 0.0)
    assert "bad_fire_at" in bad
    assert schedule_once.pending_requests(server.routines_home, "target-r") == []


# -- the web endpoints ------------------------------------------------------------------


@pytest.fixture
def sched_client(api_client, make_routine):
    make_routine(slug="weekly")
    return api_client


def test_api_arm_list_cancel(sched_client):
    c, _ = sched_client
    r = c.post("/api/routines/weekly/schedule-once", json={"fire_at": "+3d", "reason": "check"})
    assert r.status_code == 201
    oid = r.json()["one_shot"]["id"]
    lst = c.get("/api/routines/weekly/schedule-once").json()
    assert len(lst["armed"]) == 1 and lst["armed"][0]["id"] == oid
    assert c.delete(f"/api/routines/weekly/schedule-once/{oid}").status_code == 200
    assert c.get("/api/routines/weekly/schedule-once").json()["armed"] == []


def test_api_arm_rejects_bad_fire_at_and_unknown_routine(sched_client):
    c, _ = sched_client
    assert c.post("/api/routines/weekly/schedule-once",
                  json={"fire_at": "nope", "reason": "x"}).status_code == 422
    assert c.post("/api/routines/ghost/schedule-once",
                  json={"fire_at": "+1d", "reason": "x"}).status_code == 404
    assert c.delete("/api/routines/weekly/schedule-once/so-nope").status_code == 404
