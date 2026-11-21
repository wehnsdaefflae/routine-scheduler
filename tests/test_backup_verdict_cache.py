"""The console reads the backup watch's KEPT verdict; only the daemon reads the share (2026-11-21).

`/api/health/blocked` asked "is the backup still stale?" by calling `backup_watch.staleness()`
inside every request — stamp reads on the off-box share over sshfs — and was the console's slowest
route: 14 of 20 retained slow-samples caught the request thread in `newest_stamp`'s `open`. The
watch now reads the stamps once every `CHECK_EVERY_S`, in a worker thread, and keeps the verdict.
Each test here pins one property that change depends on — above all that a verdict which is
missing, failed or too old reads as UNKNOWN, never as the last `ok`.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import authed_client, make_test_server
from helpers import tmp_server
from rsched.daemon import backup_watch
from rsched.health_events import HEALTH_EVENTS_FILE
from rsched.paths import append_jsonl
from rsched.readmodels import memo
from rsched.web.operator_conditions import checker


def _stamp(root, when):
    root.mkdir(parents=True, exist_ok=True)
    (root / backup_watch.COMPLETED_STAMP).write_text(f"{when.isoformat()}\n", encoding="utf-8")
    return root


def _single_root(monkeypatch, root):
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [root])


def _holds(server, watch):
    return checker(SimpleNamespace(server=server, scheduler=SimpleNamespace(backup=watch)))


@pytest.mark.parametrize(("age_days", "holds"), [(0, False), (19, True)])
async def test_the_checker_answers_from_the_kept_verdict(tmp_path, monkeypatch, age_days, holds):
    server = tmp_server(tmp_path)
    _single_root(monkeypatch, _stamp(tmp_path / "share", datetime.now(UTC)
                                     - timedelta(days=age_days)))
    watch = backup_watch.BackupWatch(server)
    await watch.check()
    assert _holds(server, watch)("backup_stale", "") is holds


async def test_the_request_path_never_reads_the_share(tmp_path, monkeypatch):
    """The whole point: once a verdict is kept, a request must not touch a stamp."""
    server = tmp_server(tmp_path)
    _single_root(monkeypatch, _stamp(tmp_path / "share", datetime.now(UTC)))
    watch = backup_watch.BackupWatch(server)
    await watch.check()

    def no_read(*_a, **_kw):
        raise AssertionError("the request path read the backup share")

    monkeypatch.setattr(backup_watch, "staleness", no_read)
    monkeypatch.setattr(backup_watch, "newest_stamp", no_read)
    assert _holds(server, watch)("backup_stale", "") is False


def test_the_route_never_reads_the_share(tmp_path, monkeypatch):
    """The endpoint end to end: a stale-backup row is served without one stamp read."""
    server = make_test_server(tmp_path)
    (server.routines_home / ".control").mkdir(parents=True, exist_ok=True)
    append_jsonl(server.routines_home / ".control" / HEALTH_EVENTS_FILE,
                 {"event": "backup_stale", "routine": "", "run_id": "",
                  "ts": datetime.now(UTC).isoformat(), "detail": "x"})
    memo.reset()

    def no_read(*_a, **_kw):
        raise AssertionError("the request path read the backup share")

    monkeypatch.setattr(backup_watch, "staleness", no_read)
    monkeypatch.setattr(backup_watch, "newest_stamp", no_read)
    with authed_client(server) as client:
        r = client.get("/api/health/blocked")
    assert r.status_code == 200
    [row] = r.json()["rows"]
    assert row["event"] == "backup_stale"
    assert not row.get("resolved"), "no scheduler, no verdict: unknown keeps the row counting"


def test_no_verdict_yet_is_unknown_not_fresh(tmp_path):
    server = tmp_server(tmp_path)
    watch = backup_watch.BackupWatch(server)
    assert watch.current() is None
    assert _holds(server, watch)("backup_stale", "") is None
    assert checker(SimpleNamespace(server=server))("backup_stale", "") is None   # no scheduler


async def test_an_old_verdict_ages_out_to_unknown(tmp_path, monkeypatch):
    """A wedged share holds the check's thread and `_off_tick` starts no second one, so the last
    verdict stops being refreshed. Past VERDICT_MAX_AGE_S it must stop being served."""
    server = tmp_server(tmp_path)
    _single_root(monkeypatch, _stamp(tmp_path / "share", datetime.now(UTC)))
    watch = backup_watch.BackupWatch(server)
    await watch.check()
    assert watch.current() is not None
    assert watch.current()[0] == "ok"

    later = backup_watch.monotonic() + backup_watch.VERDICT_MAX_AGE_S + 1
    monkeypatch.setattr(backup_watch, "monotonic", lambda: later)
    assert watch.current() is None
    assert _holds(server, watch)("backup_stale", "") is None


async def test_a_check_that_raised_clears_the_verdict(tmp_path, monkeypatch):
    server = tmp_server(tmp_path)
    _single_root(monkeypatch, _stamp(tmp_path / "share", datetime.now(UTC)))
    watch = backup_watch.BackupWatch(server)
    await watch.check()
    assert watch.current() is not None

    def boom(**_kw):
        raise OSError("the share wedged")

    monkeypatch.setattr(backup_watch, "staleness", boom)
    await watch.check()
    assert watch.current() is None, "a failed check must not leave the last `ok` standing"


async def test_the_stamps_are_read_off_the_event_loop(tmp_path, monkeypatch):
    """The tick used to call `staleness()` on the loop itself, every 5 s: an sshfs read there
    holds every request and every fire behind it."""
    server = tmp_server(tmp_path)
    seen: list[threading.Thread] = []

    def record(**_kw):
        seen.append(threading.current_thread())
        return "ok", "fresh"

    monkeypatch.setattr(backup_watch, "staleness", record)
    await backup_watch.BackupWatch(server).check()
    assert seen and seen[0] is not threading.main_thread()


async def test_the_tick_checks_once_per_interval(tmp_path, monkeypatch):
    server = tmp_server(tmp_path)
    calls: list[int] = []

    def count(**_kw):
        calls.append(1)
        return "ok", "fresh"

    monkeypatch.setattr(backup_watch, "staleness", count)
    watch = backup_watch.BackupWatch(server)
    for _ in range(5):
        await watch.tick()
    assert len(calls) == 1

    later = backup_watch.monotonic() + backup_watch.CHECK_EVERY_S + 1
    monkeypatch.setattr(backup_watch, "monotonic", lambda: later)
    await watch.tick()
    assert len(calls) == 2


def test_a_verdict_is_served_well_inside_its_age_bound():
    """The bound must outlast several checks, or one slow read would flicker the badge."""
    assert backup_watch.VERDICT_MAX_AGE_S >= 3 * backup_watch.CHECK_EVERY_S
