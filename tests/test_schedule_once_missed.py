"""A MISSED one-shot is skipped, like a missed cron fire (D237, operator 2026-11-05: "yes, skip
missed one-shots too"): due before the daemon process started, or due while paused, it is consumed
unfired and filed as `oneshot_skipped`; one that is merely waiting still fires."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import FakeRunner
from helpers import tmp_server
from rsched import registry, schedule_once
from rsched.daemon.schedule_once import OneShotManager
from test_schedule_once import _routine


async def test_a_request_due_while_the_daemon_was_down_is_skipped(tmp_path, monkeypatch):
    """D237 (operator 2026-11-05): a missed one-shot is skipped like a missed cron fire. Armed
    before this manager (= this daemon process) existed and due by then: consumed unfired, and
    the skip is recorded."""
    filed: list[dict] = []
    monkeypatch.setattr("rsched.daemon.schedule_once.log_health_event",
                        lambda _h, event, **kw: filed.append({"event": event, **kw}))
    server = tmp_server(tmp_path)
    _routine(server)
    schedule_once.arm(server.routines_home, "oneshot", reason="x", requested_by="x",
                      fire_at=datetime.now(UTC) - timedelta(days=30))
    await OneShotManager(server, runner := FakeRunner()).tick(registry.scan(server))
    assert runner.fired == []
    assert schedule_once.pending_requests(server.routines_home, "oneshot") == []
    assert filed and filed[0]["event"] == "oneshot_skipped" and filed[0]["reason"] == "down"


async def test_a_request_due_while_paused_is_skipped_and_a_later_one_kept(tmp_path, monkeypatch):
    filed: list[dict] = []
    monkeypatch.setattr("rsched.daemon.schedule_once.log_health_event",
                        lambda _h, event, **kw: filed.append({"event": event, **kw}))
    server = tmp_server(tmp_path)
    _routine(server)
    manager = OneShotManager(server, runner := FakeRunner())
    manager.booted -= timedelta(hours=1)          # the daemon was up when both were armed
    schedule_once.arm(server.routines_home, "oneshot", reason="due", requested_by="x",
                      fire_at=datetime.now(UTC) - timedelta(minutes=5))
    schedule_once.arm(server.routines_home, "oneshot", reason="later", requested_by="x",
                      fire_at=datetime.now(UTC) + timedelta(hours=2))
    await manager.tick(registry.scan(server), paused=True)
    assert runner.fired == []
    left = schedule_once.pending_requests(server.routines_home, "oneshot")
    assert len(left) == 1 and schedule_once.read_request(left[0])["reason"] == "later"
    assert [f["reason"] for f in filed] == ["paused"]


async def test_a_request_armed_while_up_still_fires_once_due(tmp_path):
    server = tmp_server(tmp_path)
    _routine(server)
    manager = OneShotManager(server, runner := FakeRunner())
    manager.booted -= timedelta(hours=1)
    schedule_once.arm(server.routines_home, "oneshot", reason="x", requested_by="x",
                      fire_at=datetime.now(UTC) - timedelta(seconds=5))
    await manager.tick(registry.scan(server))
    assert runner.fired and runner.fired[0][0] == "oneshot"
