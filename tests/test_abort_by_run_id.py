"""Abort names a RUN, and the runner's active map is keyed per HOME (D247)."""

import asyncio
import json

from helpers import server_config
from rsched.config import RoutineConfig
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.daemon.runner_state import ActiveRun


def _server(tmp_path):
    server = server_config(routines_home=tmp_path / "routines",
                           conversations_home=tmp_path / "conversations",
                           background_home=tmp_path / "background")
    for home in (server.routines_home, server.conversations_home, server.background_home):
        home.mkdir(parents=True)
    return server


def _cfg(home, slug: str) -> RoutineConfig:
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    return RoutineConfig(slug=slug, dir=d, enabled=True)


def _state(run: ActiveRun) -> str:
    return str(json.loads((run.run_dir / "status.json").read_text(encoding="utf-8"))["state"])


def test_two_homes_one_slug_both_active_and_abort_hits_only_one(tmp_path, monkeypatch):
    """The first increment: a routine and a conversation named alike both run, and aborting
    the routine's run leaves the conversation's running.
    """
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    monkeypatch.setattr(runner, "_spawn_supervisor", lambda *a, **k: None)

    routine_id = asyncio.run(runner.fire(_cfg(server.routines_home, "shared")))
    conv_id = asyncio.run(runner.fire(_cfg(server.conversations_home, "shared")))
    assert routine_id and conv_id            # neither is refused as an overrun of the other
    assert runner.is_active("routines", "shared")
    assert runner.is_active("conversations", "shared")
    routine_run = runner.active["routines", "shared"]
    conv_run = runner.active["conversations", "shared"]

    assert asyncio.run(runner.abort(routine_id, "routines")) is True

    assert routine_run.cancelled is True and _state(routine_run) == "aborted"
    assert conv_run.cancelled is False and _state(conv_run) == "queued"
    assert runner.is_active("conversations", "shared")


def test_abort_refuses_a_run_id_that_two_homes_answer_to(tmp_path):
    """Same slug, same run-ts, two homes: the run id alone cannot say which run. Without a
    home `abort` refuses (and kills nothing); with one it hits exactly that run.
    """
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    runs = {}
    for home_kind, home in (("routines", server.routines_home),
                            ("background", server.background_home)):
        rd = home / "twin" / "runs" / "20260101-000000"
        rd.mkdir(parents=True)
        (rd / "status.json").write_text(json.dumps({"state": "queued"}), encoding="utf-8")
        runs[home_kind] = ActiveRun(slug="twin", run_id="twin:20260101-000000",
                                    run_ts="20260101-000000", run_dir=rd, home=home_kind)
        runner.active[home_kind, "twin"] = runs[home_kind]

    assert asyncio.run(runner.abort("twin:20260101-000000")) is False
    assert all(not run.cancelled for run in runs.values())

    assert asyncio.run(runner.abort("twin:20260101-000000", "background")) is True
    assert runs["background"].cancelled is True
    assert runs["routines"].cancelled is False


def test_abort_ignores_a_run_id_that_is_not_the_live_run(tmp_path):
    """Aborting by SLUG stopped whichever run of that routine was live. By run id, a stale id
    stops nothing — the run the operator was looking at is already gone.
    """
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    rd = server.routines_home / "scrape" / "runs" / "20260101-000000"
    rd.mkdir(parents=True)
    (rd / "status.json").write_text(json.dumps({"state": "queued"}), encoding="utf-8")
    live = ActiveRun(slug="scrape", run_id="scrape:20260101-000000", run_ts="20260101-000000",
                     run_dir=rd, home="routines")
    runner.active["routines", "scrape"] = live

    assert asyncio.run(runner.abort("scrape:20251231-235959", "routines")) is False
    assert live.cancelled is False
    assert asyncio.run(runner.abort("scrape:20260101-000000", "routines")) is True
    assert live.cancelled is True
