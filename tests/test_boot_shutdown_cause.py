"""One boot retains shutdown attribution across homes with colliding slugs."""

import asyncio
import contextlib

import yaml

from conftest import make_test_server, mk_run
from rsched.daemon import restart
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.daemon.scheduler import Scheduler
from rsched.paths import read_json


async def test_one_boot_attributes_orphans_in_all_three_homes(tmp_path, monkeypatch):
    server = make_test_server(tmp_path)
    directories = []
    for home in (server.routines_home, server.conversations_home, server.background_home):
        directory = home / "same-slug"
        directory.mkdir(parents=True)
        (directory / "main.md").write_text("# Test recipe\n", encoding="utf-8")
        (directory / "routine.yaml").write_text(
            yaml.safe_dump({"slug": "same-slug", "description": "Recovery test",
                            "schedule": {"cron": ""}}), encoding="utf-8")
        mk_run(directory, "20260701-070000", "running", pid=999999)
        directories.append(directory)
    restart.mark_deliberate_shutdown(server, "test restart")
    scheduler = Scheduler(server, Runner(server, EventBus()), EventBus())
    recovered = asyncio.Event()

    async def stop_after_recovery():
        recovered.set()
        await asyncio.Future()

    monkeypatch.setattr(scheduler.detached, "reconcile", stop_after_recovery)
    task = asyncio.create_task(scheduler.run_forever())
    try:
        await asyncio.wait_for(recovered.wait(), timeout=5)
        for directory in directories:
            run_dir = directory / "runs" / "20260701-070000"
            assert read_json(run_dir / "status.json")["state"] == "aborted"
            assert "daemon restart" in (run_dir / "result.md").read_text()
        assert not restart.shutdown_mark_path(server).exists()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
