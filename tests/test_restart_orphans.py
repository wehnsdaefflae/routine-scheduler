"""F746: an EXTERNAL restart leaves a breadcrumb, and the run it killed is resumed not aborted.

The defect these pin, measured on the live instance 2026-10-22 (R2793, R2800): 79 orphaned runs
over 50 boots, and of the 20 events carrying a cause at all, 15 read `unknown` and NOT ONE ever
read `daemon_restart` — because `mark_deliberate_shutdown` had a single caller, the daemon's own
self-update path. Every deploy, `systemctl restart` and container replacement therefore reaped
every live run as an unexplained death, and `self-audit:20261021-223212` was closed `aborted` at
turn 240 with 5125 s of work in it.

The operator's ruling the same day: *"Drain in-flight runs before any restart, and resume rather
than abort"*. A drain cannot hold an external SIGTERM (uvicorn bounds its graceful shutdown to
10 s and a run lasts hours), so the equivalent is that the WORK continues on the next boot.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from conftest import mk_run
from helpers import health_events, tmp_server
from rsched.config import ServerConfig, load_routine
from rsched.daemon import pause, reap_resume, restart, runner_reap
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.paths import atomic_write_json, read_json
from rsched.registry import read_run, run_index, scan


def _server(tmp_path) -> ServerConfig:
    return tmp_server(tmp_path, create=False)


def _orphan(make_routine, slug: str, state: str = "running"):
    """A routine with one run the registry reads as alive under a pid that is not."""
    d = make_routine(slug=slug)
    mk_run(d, "20260701-070000", state, pid=999999)
    return d


# --- half 1: the mark is written on ANY graceful exit ------------------------------------


def test_an_external_sigterm_leaves_a_mark_the_next_boot_can_read(tmp_path):
    """The whole F746 gap in one assertion: nothing asked the daemon to restart itself, so
    `trigger_shutdown` never ran — and the exit must STILL say it was deliberate."""
    server = _server(tmp_path)
    assert not restart.shutdown_mark_path(server).exists()
    restart.mark_graceful_exit(server)
    mark = read_json(restart.shutdown_mark_path(server))
    assert mark["reason"] == "external shutdown (SIGTERM)"


def test_a_self_update_restart_keeps_its_more_specific_reason(tmp_path):
    """Both write the same file in the same exit: `trigger_shutdown` marks it three lines
    before signalling, and the lifespan's shutdown half runs afterwards. Last writer wins
    would lose the specific reason, so the general one must stand down."""
    server = _server(tmp_path)
    restart.mark_deliberate_shutdown(server, "self-update restart")
    restart.mark_graceful_exit(server)
    assert read_json(restart.shutdown_mark_path(server))["reason"] == "self-update restart"


def test_the_lifespan_shutdown_half_is_what_writes_it(tmp_path, monkeypatch):
    """Pins the SEAM, not just the function: the mark has to be written by the one exit path
    every graceful shutdown reaches. An appwiring that stops calling it is the defect coming
    back, and it would be invisible to every other test here."""
    import inspect

    from rsched.web import appwiring
    source = inspect.getsource(appwiring._make_lifespan)
    before, _, after = source.partition("yield")
    assert "mark_graceful_exit" in after, "the mark must be written AFTER the yield"
    assert "mark_graceful_exit" not in before


# --- half 2: the run a restart killed is RESUMED -----------------------------------------


def test_a_restart_killed_run_is_collected_for_resume(make_routine, tmp_path):
    """With the breadcrumb present the boot knows the death was a restart — so the run is
    both closed out (the record is honest) AND handed back for re-opening."""
    server = _server(tmp_path)
    _orphan(make_routine, "restarted")
    restart.mark_deliberate_shutdown(server, "external shutdown (SIGTERM)")
    runner = Runner(server, EventBus())
    resumable: list = []
    assert runner_reap.recover_orphans(runner, scan(server), resumable) == 1
    assert [r[1].run_id for r in resumable] == ["restarted:20260701-070000"]


def test_an_unexplained_death_is_never_resumed(make_routine, tmp_path):
    """The honest residue stays closed. A crash, an OOM or a kill nobody recorded is exactly
    the case where the daemon knows too little to start anything again — resuming there would
    turn a crash loop into a run loop, and `unknown` exists to say nothing was established."""
    server = _server(tmp_path)
    _orphan(make_routine, "crashed")
    runner = Runner(server, EventBus())
    resumable: list = []
    assert runner_reap.recover_orphans(runner, scan(server), resumable) == 1
    assert resumable == []
    assert read_run((tmp_path / "routines" / "crashed" / "runs" / "20260701-070000"),
                    "crashed").state == "aborted"


def test_the_reap_without_a_collector_behaves_exactly_as_before(make_routine, tmp_path):
    """Four scheduler tests and the sync boot pass call the reap with two arguments. The
    collector is optional precisely so this stays true, and a reap that needed a loop would
    raise in every one of them."""
    server = _server(tmp_path)
    _orphan(make_routine, "legacy")
    restart.mark_deliberate_shutdown(server, "self-update restart")
    runner = Runner(server, EventBus())
    assert runner_reap.recover_orphans(runner, scan(server)) == 1


@pytest.mark.parametrize("state", ["waiting_user", "paused"])
def test_a_run_parked_on_a_human_is_not_re_opened(make_routine, tmp_path, state):
    """A parked run's question is durable on the Decisions page. Re-entering the wait would
    re-ask the operator something he is already looking at, so the close-out stands."""
    d = _orphan(make_routine, f"parked-{state}", state=state)
    run = run_index(d, f"parked-{state}")[0]
    assert "parked" in (reap_resume.restart_resume_blocker(run) or "")


def test_the_resume_is_capped_so_a_boot_loop_cannot_storm(make_routine, tmp_path):
    """Two boots 49 minutes apart orphaned the SAME two runs on 2026-10-22, so one resume is
    too few — but a daemon that boot-loops must not re-open every run forever. The count
    lives in the run dir and is read back."""
    d = _orphan(make_routine, "looped")
    run = run_index(d, "looped")[0]
    assert reap_resume.restart_resume_blocker(run) is None
    atomic_write_json(run.dir / "restart-resume.json",
                      {"resumes": reap_resume.RESTART_RESUMES})
    assert "already re-opened" in (reap_resume.restart_resume_blocker(run) or "")


async def test_resume_asks_the_runner_and_leaves_the_leg_a_note(make_routine, tmp_path):
    """The resumed leg must STATE what happened: a leg that continues as if nothing did spends
    its turns rediscovering that the daemon went down and that its children are gone."""
    server = _server(tmp_path)
    d = _orphan(make_routine, "noted")
    cfg, _ = load_routine(d)
    runner = Runner(server, EventBus())
    run = run_index(d, "noted")[0]
    asked: list = []

    async def _resume(c, ts, **kw):
        asked.append((c.slug, ts, kw.get("reason")))
        return f"{c.slug}:{ts}"
    runner.resume = _resume          # type: ignore[method-assign]
    assert reap_resume.resume_after_restart(runner, cfg, run, "daemon restart (deploy)")
    await asyncio.sleep(0)
    assert asked == [("noted", "20260701-070000", "restart-resume")]
    notes = [p.read_text(encoding="utf-8") for p in (d / "inbox").glob("msg-*.json")]
    assert len(notes) == 1 and "AUTOMATIC RECOVERY" in notes[0]
    assert "did not survive" in notes[0]          # the children are gone: say so
    assert read_json(run.dir / "restart-resume.json")["resumes"] == 1


async def test_a_global_pause_holds_the_restart_resume(make_routine, tmp_path):
    """`daemon/pause.py`'s docstring is the CONTRACT of what the daemon does on its own, and
    F721 is what omitting one costs. A third automatic leg obeys the same hold."""
    server = _server(tmp_path)
    d = _orphan(make_routine, "held")
    cfg, _ = load_routine(d)
    atomic_write_json(pause.sentinel_path(server), {"ts": "now"})
    runner = Runner(server, EventBus())
    run = run_index(d, "held")[0]
    runner.resume = lambda *a, **kw: pytest.fail("a paused instance must start nothing")  # type: ignore[method-assign]
    assert not reap_resume.resume_after_restart(runner, cfg, run, "daemon restart")
    assert not list((d / "inbox").glob("msg-*.json"))


async def test_resume_all_drives_every_row_independently(make_routine, tmp_path):
    """One routine that refuses must not strand the ones after it — the same rule the
    close-out loop follows, for the same reason."""
    server = _server(tmp_path)
    rows = []
    for slug in ("first", "second"):
        d = _orphan(make_routine, slug)
        cfg, _ = load_routine(d)
        rows.append((cfg, run_index(d, slug)[0], "daemon restart"))
    runner = Runner(server, EventBus())
    calls: list = []

    async def _resume(c, ts, **kw):
        calls.append(c.slug)
        if c.slug == "first":
            raise OSError("run dir refuses the write")
    runner.resume = _resume          # type: ignore[method-assign]
    assert reap_resume.resume_all(runner, rows) == 2
    await asyncio.sleep(0)
    assert calls == ["first", "second"]


# --- half 3: the drain the deployer runs BEFORE the restart -------------------------------


class _DrainArgs:
    def __init__(self, *, timeout: int = 0, poll: int = 0, resume: bool = False):
        self.timeout, self.poll, self.resume = timeout, poll, resume


def test_drain_waits_while_a_run_is_in_flight_and_refuses_to_report_quiet(
        make_routine, tmp_path, monkeypatch):
    """The operator's first clause. A deploy that restarts now would interrupt this run, so the
    command exits NON-ZERO and names it — `rsched drain && docker compose up -d` then stops,
    which is the whole point: the deployer decides, informed."""
    from rsched import cli_runctl
    server = _server(tmp_path)
    _orphan(make_routine, "busy")
    monkeypatch.setattr(cli_runctl, "load_server_config", lambda: (server, []))
    assert cli_runctl.cmd_drain(_DrainArgs(timeout=0)) == 1
    assert cli_runctl.active_runs(server) == ["busy:20260701-070000"]
    # and the pause is in place, so nothing NEW starts while the deployer decides
    assert pause.paused(server)


def test_drain_reports_quiet_and_leaves_the_pause_for_the_deploy(make_routine, tmp_path,
                                                                 monkeypatch):
    """On a quiet instance it exits 0 — and the pause STAYS, because the deploy is still to
    come: the replacement process reads the same sentinel, so no fire slips into the 30 seconds
    before it is replaced."""
    from rsched import cli_runctl
    server = _server(tmp_path)
    make_routine(slug="idle")                      # no run dir: nothing in flight
    monkeypatch.setattr(cli_runctl, "load_server_config", lambda: (server, []))
    assert cli_runctl.cmd_drain(_DrainArgs(timeout=0)) == 0
    assert pause.paused(server)
    assert cli_runctl.cmd_drain(_DrainArgs(resume=True)) == 0
    assert not pause.paused(server)


def test_drain_sees_a_conversation_as_in_flight_too(make_routine, tmp_path):
    """All three homes: a conversation and a detached background task ARE runs, and a deploy
    that kills a conversation mid-dialogue is exactly the case `restart.PARKED` protects
    against on the daemon's own path."""
    from rsched import cli_runctl
    server = _server(tmp_path)
    talking = server.conversations_home / "chatty"
    (talking / "state").mkdir(parents=True)
    (talking / "main.md").write_text("# conversation\n", encoding="utf-8")
    (talking / "routine.yaml").write_text(
        "slug: chatty\ndescription: a conversation\nschedule:\n  cron: ''\n", encoding="utf-8")
    mk_run(talking, "20260701-080000", "running", pid=999999)
    assert cli_runctl.active_runs(server) == ["chatty:20260701-080000"]


# --- the boot drives it ------------------------------------------------------------------


async def test_the_boot_re_opens_what_the_restart_killed(make_routine, tmp_path, monkeypatch):
    """End to end over the real boot: mark, orphan, boot — and the run comes back rather than
    staying the `aborted` row the fleet reads as a failure."""
    from rsched.daemon.scheduler import Scheduler
    server = _server(tmp_path)
    _orphan(make_routine, "booted")
    restart.mark_deliberate_shutdown(server, "external shutdown (SIGTERM)")
    scheduler = Scheduler(server, Runner(server, EventBus()), EventBus())
    woken: list = []
    monkeypatch.setattr(reap_resume, "resume_all",
                        lambda runner, rows: woken.extend(r[1].run_id for r in rows))
    reached = asyncio.Event()

    async def _stop_here():
        reached.set()
        await asyncio.Future()
    monkeypatch.setattr(scheduler.detached, "reconcile", _stop_here)
    task = asyncio.create_task(scheduler.run_forever())
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        assert woken == ["booted:20260701-070000"]
        assert health_events(server.routines_home, routine="booted")[0]["cause"] \
            == "daemon_restart"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
