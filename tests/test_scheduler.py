"""Scheduler fire logic and Runner subprocess supervision (stub engines, real processes)."""

import asyncio
import json
import logging
import signal
from datetime import UTC, datetime, timedelta

import rsched.daemon.scheduler as sched_mod
from conftest import FakeRunner
from rsched.config import ServerConfig, load_routine
from rsched.daemon import runner_reap, runner_state
from rsched.daemon.events import EventBus
from rsched.daemon.runner import Runner
from rsched.daemon.runner_state import _notable_stderr
from rsched.daemon.scheduler import Scheduler
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json
from rsched.registry import read_run, scan


def _server(tmp_path, max_concurrent=2) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.max_concurrent_runs = max_concurrent
    return s


def test_rescan_keeps_owed_fires(make_routine, tmp_path):
    make_routine(slug="owed")
    sched = Scheduler(_server(tmp_path), FakeRunner(), EventBus())
    sched.rescan()
    assert "owed" in sched.next_fires and sched.next_fires["owed"] > datetime.now(UTC)
    past = datetime.now(UTC) - timedelta(seconds=30)
    sched.next_fires["owed"] = past
    sched.rescan()
    assert sched.next_fires["owed"] == past  # a due fire survives the rescan


async def test_boot_catchup_fires_run_once(make_routine, tmp_path):
    d = make_routine(slug="catchup")
    text = (d / "routine.yaml").read_text().replace("catchup: skip", "catchup: run_once")
    (d / "routine.yaml").write_text(text)
    make_routine(slug="skipper")  # default skip policy → no catch-up
    fr = FakeRunner()
    sched = Scheduler(_server(tmp_path), fr, EventBus())
    sched.rescan()
    await sched.boot_catchup()
    assert fr.fired == [("catchup", "catchup")]


async def test_fire_on_due_tick(make_routine, tmp_path, monkeypatch):
    make_routine(slug="ticker")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    fr = FakeRunner()
    sched = Scheduler(_server(tmp_path), fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.next_fires["ticker"] = datetime.now(UTC) - timedelta(seconds=1)
    await asyncio.sleep(0.1)
    task.cancel()
    assert ("ticker", "schedule") in fr.fired
    assert sched.next_fires["ticker"] > datetime.now(UTC)  # advanced past the fire


async def test_paused_tick_skips_fires_but_advances(make_routine, tmp_path, monkeypatch):
    """D34: with the pause sentinel set, a due tick fires NOTHING — but the fire table
    still advances (resuming must not backlog-fire), and the snapshot reports the flag."""
    from rsched.daemon import pause
    make_routine(slug="ticker")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    server = _server(tmp_path)
    pause.set_paused(server, True)
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.next_fires["ticker"] = datetime.now(UTC) - timedelta(seconds=1)
    await asyncio.sleep(0.1)
    task.cancel()
    assert fr.fired == []                                    # paused: nothing fired
    assert sched.next_fires["ticker"] > datetime.now(UTC)    # yet the table advanced
    assert sched.snapshot()["paused"] is True
    pause.set_paused(server, False)                          # resume: flag clears
    assert sched.snapshot()["paused"] is False


# --- D71: group schedules — member-cron suppression + the group's own fire ---------


def test_rescan_suppresses_scheduled_group_members(make_routine, tmp_path):
    """A member of a group WITH a cron loses its own fire-table entry (one fire path);
    a member of an UNSCHEDULED group keeps firing on its own cron."""
    from rsched import groups
    make_routine(slug="chained")
    make_routine(slug="loose")
    server = _server(tmp_path)
    groups.create(server.routines_home, name="Plain", members=[{"slug": "loose"}])          # no cron
    grp = groups.create(server.routines_home, name="Sched", members=[{"slug": "chained"}],
                        cron="0 7 * * *", tz="UTC")
    sched = Scheduler(server, FakeRunner(), EventBus())
    sched.rescan()
    assert "chained" not in sched.next_fires          # suppressed: group-managed
    assert "loose" in sched.next_fires                # unscheduled group changes nothing
    assert grp["id"] in sched.group_next_fires        # the group has its own fire
    assert sched.snapshot()["group_next_fires"]
    # removing the schedule restores the member's own cron on the next rescan
    groups.update(server.routines_home, grp["id"], cron="")
    sched.rescan()
    assert "chained" in sched.next_fires
    assert grp["id"] not in sched.group_next_fires


async def test_boot_catchup_skips_group_managed_members(make_routine, tmp_path):
    from rsched import groups
    d = make_routine(slug="gcatch")
    text = (d / "routine.yaml").read_text().replace("catchup: skip", "catchup: run_once")
    (d / "routine.yaml").write_text(text)
    server = _server(tmp_path)
    groups.create(server.routines_home, name="Sched", members=[{"slug": "gcatch"}],
                  cron="0 7 * * *", tz="UTC")
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    sched.rescan()
    await sched.boot_catchup()
    assert fr.fired == []          # its own cron is suppressed, catch-up included


async def test_due_group_cron_arms_the_chain_and_fires_member_zero(make_routine, tmp_path,
                                                                   monkeypatch):
    """The group cron auto-arms the D53 chain: the scheduler arms it at the due tick and
    the GroupRunManager fires member 0 (the rest chain on completion, as when armed by
    hand). A second due fire while the chain is in flight is skipped (overrun rule)."""
    from rsched import group_runs, groups
    make_routine(slug="first")
    make_routine(slug="second")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    server = _server(tmp_path)
    grp = groups.create(server.routines_home, name="Chain", members=[{"slug": "first"}, {"slug": "second"}],
                        cron="0 7 * * *", tz="UTC")
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.group_next_fires[grp["id"]] = datetime.now(UTC) - timedelta(seconds=1)
    assert await _wait_for(lambda: ("first", "group") in fr.fired)
    task.cancel()
    assert sched.group_next_fires[grp["id"]] > datetime.now(UTC)   # advanced past the fire
    rec = group_runs.read(server.routines_home, grp["id"])
    assert rec is not None and rec["armed_by"] == "schedule"
    assert rec["current_run"].startswith("first:")
    assert ("second", "group") not in fr.fired      # sequential: member 1 waits its turn


# --- Runner with stub engine processes -------------------------------------------


def _stub_engine(monkeypatch, script: str):
    """Replace the engine subprocess with a bash stub. The stub runs with cwd=<routine dir>;
    $1 is the run ts (script references runs/$TS/...).

    The signature MIRRORS engine_cmd's, server argument included: a stub that silently stops
    matching the real one is how F394 happened (the F393 split moved engine_cmd, `from …
    import` froze the old reference, and the patch quietly stopped taking). The real
    engine_cmd now refuses a source-less ServerConfig outright, so the same slip fails loudly
    instead of running the fixture against production.
    """

    def cmd(server, target, run_ts, *, resume=False):
        return ["bash", "-c", script.replace("{TS}", run_ts)]

    monkeypatch.setattr(runner_state, "engine_cmd", cmd)


async def _wait_for(cond, wait_s=5.0):
    for _ in range(int(wait_s / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_fire_reap_and_overrun(make_routine, tmp_path, monkeypatch):
    d = make_routine(slug="stub")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 0.2")
    runner = Runner(_server(tmp_path), EventBus())
    run_id = await runner.fire(cfg)
    assert run_id and runner.is_active("stub")
    st = read_json(d / "runs" / run_id.split(":")[1] / "status.json")
    assert st["state"] == "queued"
    assert await runner.fire(cfg) is None  # overrun skipped
    assert await _wait_for(lambda: not runner.is_active("stub"))
    # stub never wrote a real status → daemon closed it out with a synthetic finish
    run_dir = d / "runs" / run_id.split(":")[1]
    info = read_run(run_dir, "stub")
    assert info.state == "failed" and "engine exited" in info.summary
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert events[-1]["type"] == "finish"


async def test_concurrency_cap_queues_spawn(make_routine, tmp_path, monkeypatch):
    d1 = make_routine(slug="one")
    d2 = make_routine(slug="two")
    cfg1, _ = load_routine(d1)
    cfg2, _ = load_routine(d2)
    _stub_engine(monkeypatch, "sleep 0.3")
    runner = Runner(_server(tmp_path, max_concurrent=1), EventBus())
    await runner.fire(cfg1)
    await runner.fire(cfg2)
    assert await _wait_for(lambda: runner.active["one"].proc is not None)
    assert runner.active["two"].proc is None  # queued: no process until a slot frees
    assert await _wait_for(lambda: "one" not in runner.active)
    assert await _wait_for(lambda: runner.active["two"].proc is not None)
    assert await _wait_for(lambda: not runner.active)


async def test_waiting_user_releases_slot(make_routine, tmp_path, monkeypatch):
    d1 = make_routine(slug="asker")
    d2 = make_routine(slug="worker")
    cfg1, _ = load_routine(d1)
    cfg2, _ = load_routine(d2)
    monkeypatch.setattr(runner_state, "STATUS_POLL_S", 0.03)
    _stub_engine(monkeypatch,
                 'printf \'{"state": "waiting_user", "pid": 1}\' > runs/{TS}/status.json.tmp '
                 '&& mv runs/{TS}/status.json.tmp runs/{TS}/status.json && sleep 0.6')
    runner = Runner(_server(tmp_path, max_concurrent=1), EventBus())
    await runner.fire(cfg1)
    assert await _wait_for(lambda: runner.active["asker"].proc is not None)
    await runner.fire(cfg2)
    # asker parks in waiting_user → its slot frees → worker spawns while asker still lives
    assert await _wait_for(lambda: runner.active.get("worker") and runner.active["worker"].proc)
    assert runner.is_active("asker")
    assert await _wait_for(lambda: not runner.active, wait_s=8)


async def test_abort_active_run(make_routine, tmp_path, monkeypatch):
    d = make_routine(slug="abortee")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 30")
    monkeypatch.setattr(runner_state, "KILL_GRACE_S", 1)
    runner = Runner(_server(tmp_path), EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: runner.active["abortee"].proc is not None)
    assert await runner.abort("abortee") is True
    assert await _wait_for(lambda: not runner.active)
    assert await runner.abort("abortee") is False


def test_recover_orphans(make_routine, tmp_path):
    d = make_routine(slug="orphan")
    run_dir = d / "runs" / "20260701-070000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "orphan:20260701-070000", "state": "running", "pid": 999999})
    (run_dir / "transcript.jsonl").write_text(json.dumps({"type": "header"}) + "\n")
    runner = Runner(_server(tmp_path), EventBus())
    fixed = runner_reap.recover_orphans(runner, scan(_server(tmp_path)))
    assert fixed == 1
    info = read_run(run_dir, "orphan")
    assert info.state == "failed" and "orphaned" in info.summary


def test_notable_stderr_extracts_only_warnings_and_errors():
    # Info/debug chatter is dropped; WARNING/ERROR/CRITICAL/traceback lines are kept, tail-first.
    assert _notable_stderr(b"") == ""
    assert _notable_stderr(b"2026 rsched INFO run_started\n2026 rsched INFO run_finished") == ""
    out = _notable_stderr(b"2026 rsched INFO ok\n"
                          b"2026 rsched.util_stats WARNING snapshot write to /x failed: boom\n"
                          b"2026 rsched INFO more")
    assert "WARNING" in out and "snapshot write to /x failed" in out and "INFO" not in out
    assert "Traceback (most recent call last)" in _notable_stderr(
        b"Traceback (most recent call last):\n  File ...\nValueError: x")
    # A chatty run can't flood the log: only the tail of notable lines is kept.
    flood = b"\n".join(f"2026 rsched ERROR e{i}".encode() for i in range(50))
    kept = _notable_stderr(flood, max_lines=12)
    assert kept.count("ERROR") == 12 and "e49" in kept and "e0 " not in kept


async def test_reap_surfaces_clean_exit_diagnostics(make_routine, tmp_path, monkeypatch, caplog):
    # A run that finishes cleanly but logged a WARNING (stdout is DEVNULL, stderr otherwise
    # dropped) must still leave that line in the daemon log — the F97 breadcrumb that vanished.
    d = make_routine(slug="warner")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch,
                 'printf \'{"state": "finished", "pid": 1}\' > runs/{TS}/status.json.tmp '
                 '&& mv runs/{TS}/status.json.tmp runs/{TS}/status.json '
                 '&& echo "2026 rsched.util_stats WARNING util-stats snapshot write failed: boom" 1>&2')
    runner = Runner(_server(tmp_path), EventBus())
    with caplog.at_level(logging.WARNING, logger="rsched.runner"):
        await runner.fire(cfg)
        assert await _wait_for(lambda: not runner.is_active("warner"))
    surfaced = [r.getMessage() for r in caplog.records if "finished but logged" in r.getMessage()]
    assert surfaced and "util-stats snapshot write failed" in surfaced[0]


# --- R108 residual (F268): the post-finish inbox sweep ---------------------------------


async def test_reap_sweeps_stranded_user_message_and_resumes(make_routine, tmp_path,
                                                             monkeypatch):
    """A USER message that raced the finish (landed after the engine's last inbox check)
    re-opens the run from the reap via the same terminal resume a message to an idle
    conversation takes — and ONLY user messages do: a report delivery beside it stays for
    the schedule, and once the message is consumed nothing loops."""
    d = make_routine(slug="strand")
    cfg, _ = load_routine(d)
    atomic_write_json(d / "inbox" / "msg-20260805T1200-x1.json",
                      {"text": "also do X", "ts": "t", "via": "conversation"})
    atomic_write_json(d / "inbox" / "msg-rep-R999.json",
                      {"text": "REPORT R999", "ts": "t", "via": "report",
                       "report": "R999", "from": "sender"})
    # leg 1 finishes with the message still queued (the race); leg 2 — the sweep's resume
    # — consumes it the way a real boot drain would
    _stub_engine(monkeypatch,
                 'echo x >> legs; if [ "$(wc -l < legs)" -ge 2 ]; then rm -f inbox/msg-2026*.json; fi; '
                 'printf \'{"state": "finished", "pid": 1}\' > runs/{TS}/status.json.tmp '
                 '&& mv runs/{TS}/status.json.tmp runs/{TS}/status.json')
    runner = Runner(_server(tmp_path), EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: (d / "legs").exists()
                           and len((d / "legs").read_text().splitlines()) >= 2)
    assert await _wait_for(lambda: not runner.active)
    await asyncio.sleep(0.3)                       # room for a (wrong) third leg to appear
    assert len((d / "legs").read_text().splitlines()) == 2       # exactly one re-open
    assert not list((d / "inbox").glob("msg-2026*.json"))        # consumed by the resume
    assert list((d / "inbox").glob("msg-rep-*.json"))            # the report waited


async def test_reap_sweep_ignores_non_user_messages(make_routine, tmp_path, monkeypatch):
    """A finish with ONLY report/trigger-style deliveries queued wakes nothing — those
    wait for the schedule (or the routine's own report trigger)."""
    d = make_routine(slug="quiet")
    cfg, _ = load_routine(d)
    atomic_write_json(d / "inbox" / "msg-rep-R1000.json",
                      {"text": "REPORT R1000", "ts": "t", "via": "report",
                       "report": "R1000", "from": "sender"})
    _stub_engine(monkeypatch,
                 'echo x >> legs; '
                 'printf \'{"state": "finished", "pid": 1}\' > runs/{TS}/status.json.tmp '
                 '&& mv runs/{TS}/status.json.tmp runs/{TS}/status.json')
    runner = Runner(_server(tmp_path), EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: not runner.active)
    await asyncio.sleep(0.3)
    assert len((d / "legs").read_text().splitlines()) == 1       # no wake
    assert list((d / "inbox").glob("msg-rep-*.json"))


# --- F188: a user cancel must not masquerade as a crash in the health stream -----------


def _health_events(server, routine):
    path = server.routines_home / ".control" / "health-events.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if json.loads(ln)["routine"] == routine]


async def test_user_cancel_logs_run_canceled_not_orphaned(make_routine, tmp_path, monkeypatch):
    """An abort the USER requested that kills the engine before it can write its own
    finish must land in the health stream as run_canceled — not as an orphaned_run crash
    (payload shape identical)."""
    d = make_routine(slug="cancelee")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 30")
    monkeypatch.setattr(runner_state, "KILL_GRACE_S", 1)
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: runner.active["cancelee"].proc is not None)
    assert await runner.abort("cancelee") is True
    assert await _wait_for(lambda: not runner.active)
    mine = _health_events(server, "cancelee")
    assert mine and mine[-1]["event"] == "run_canceled"
    assert all(e["event"] != "orphaned_run" for e in mine)
    assert "engine exited" in mine[-1]["detail"] and mine[-1]["run_id"].startswith("cancelee:")
    # F422: the exit status is a FIELD, not prose. The two close-out events differ by who
    # asked for the death, not by how the process died — so a sweep for "did anything die by
    # signal in this window" is only answerable from `rc`. Five real rc=-9 deaths were logged
    # as run_canceled and read as a clean window, because the signal sat inside `detail`.
    assert mine[-1]["rc"] in (-signal.SIGTERM, -signal.SIGKILL)
    assert f"rc={mine[-1]['rc']}" in mine[-1]["detail"]


def test_dead_pid_recovery_still_logs_orphaned_run(make_routine, tmp_path):
    """The default close-out path is untouched: a genuinely dead run recovered at boot
    (no user cancel anywhere) keeps the orphaned_run event."""
    d = make_routine(slug="orphan2")
    run_dir = d / "runs" / "20260701-070000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "orphan2:20260701-070000", "state": "running", "pid": 999999})
    (run_dir / "transcript.jsonl").write_text(json.dumps({"type": "header"}) + "\n")
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    assert runner_reap.recover_orphans(runner, scan(server)) == 1
    events = _health_events(server, "orphan2")
    assert [e["event"] for e in events] == ["orphaned_run"]
    # a boot-time orphan has no process left to report on: the optional fields are DROPPED
    # rather than written as nulls every reader would then have to handle
    assert "rc" not in events[0] and "vm_hwm_kb" not in events[0]


# --- D99: a kernel-killed run (rc=-9) auto-resumes exactly once ------------------------


async def test_sigkilled_run_auto_resumes_exactly_once(make_routine, tmp_path, monkeypatch):
    """rc=-9 without an authored finish and without a user cancel gets ONE automatic
    in-place resume (D99-A): the run-dir marker caps the retry, the recovery note lands
    in the inbox via=background (the channel a resumed leg's boot drains), and a second
    kill leaves the run failed — no third leg."""
    d = make_routine(slug="oomer")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "echo x >> legs; kill -9 $$")
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: (d / "legs").exists()
                           and len((d / "legs").read_text().splitlines()) >= 2)
    assert await _wait_for(lambda: not runner.active)
    await asyncio.sleep(0.3)                       # room for a (wrong) third leg to appear
    assert len((d / "legs").read_text().splitlines()) == 2       # exactly one retry
    run_dir = next((d / "runs").iterdir())
    assert (run_dir / "sigkill-retry.json").is_file()            # the cap
    notes = [json.loads(p.read_text(encoding="utf-8"))
             for p in (d / "inbox").glob("msg-*.json")]
    recov = [n for n in notes if "AUTOMATIC RECOVERY" in n.get("text", "")]
    assert recov and recov[0].get("via") == "background"         # resumed-boot drainable
    mine = _health_events(server, "oomer")
    assert [e["event"] for e in mine] == ["orphaned_run", "orphaned_run"]
    assert read_run(run_dir, "oomer").state == "failed"          # second kill: stays failed


async def test_user_cancel_sigkill_never_retries(make_routine, tmp_path, monkeypatch):
    """An abort the USER escalated to SIGKILL is a cancel, not a crash — no auto-resume
    (the F188 attribution gates D99's retry too)."""
    d = make_routine(slug="cancelee2")
    cfg, _ = load_routine(d)
    # TERM-immune: the trap ignores SIGTERM and the loop outlives its killed sleep
    # children, so the abort escalates to SIGKILL after the grace period (rc=-9).
    _stub_engine(monkeypatch, "echo x >> legs; trap '' TERM; while true; do sleep 0.2; done")
    monkeypatch.setattr(runner_state, "KILL_GRACE_S", 0.5)
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    await runner.fire(cfg)
    assert await _wait_for(lambda: runner.active["cancelee2"].proc is not None)
    assert await _wait_for((d / "legs").exists)
    assert await runner.abort("cancelee2") is True
    assert await _wait_for(lambda: not runner.active)
    await asyncio.sleep(0.3)
    assert len((d / "legs").read_text().splitlines()) == 1       # no retry leg
    run_dir = next((d / "runs").iterdir())
    assert not (run_dir / "sigkill-retry.json").exists()


# --- F276/R213: a due cron fire that produces no run must be visible in the audit stream ---


async def test_refused_scheduled_fire_logs_health_event(make_routine, tmp_path, monkeypatch):
    """A SCHEDULED fire refused because the routine is still active (overrun) emits a
    `fire_refused` health event, so a routine that goes chronically un-fired (R213:
    self-audit dark two days) is visible to audit consumers — not just a log.info line."""
    d = make_routine(slug="overfirer")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 30")
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    first = await runner.fire(cfg, reason="schedule")
    assert first is not None
    assert await _wait_for(lambda: runner.active["overfirer"].proc is not None)
    # second scheduled fire collides with the still-active first → refused
    assert await runner.fire(cfg, reason="schedule") is None
    mine = _health_events(server, "overfirer")
    assert mine and mine[-1]["event"] == "fire_refused"
    assert mine[-1]["run_id"] == "" and "overrun" in mine[-1]["detail"]
    await runner.abort("overfirer")


async def test_non_scheduled_overrun_stays_quiet(make_routine, tmp_path, monkeypatch):
    """A resume/trigger/manual overrun is expected and must NOT spam the health stream —
    only the scheduled (cron) path logs fire_refused."""
    d = make_routine(slug="resumer")
    cfg, _ = load_routine(d)
    _stub_engine(monkeypatch, "sleep 30")
    server = _server(tmp_path)
    runner = Runner(server, EventBus())
    assert await runner.fire(cfg, reason="schedule") is not None
    assert await _wait_for(lambda: runner.active["resumer"].proc is not None)
    assert await runner.fire(cfg, reason="trigger") is None
    hpath = server.routines_home / ".control" / "health-events.jsonl"
    events = _health_events(server, "resumer") if hpath.exists() else []
    assert all(e["event"] != "fire_refused" for e in events)
    await runner.abort("resumer")


async def test_due_group_fire_while_in_flight_emits_refused_event(make_routine, tmp_path,
                                                                  monkeypatch):
    """F316: a due scheduled group fire that finds the previous chain still in flight is
    REFUSED — and that refusal reaches the health-event stream (a wedged chain otherwise
    starves the whole group with only a log.info line as witness)."""
    import json

    from rsched import groups
    make_routine(slug="first")
    monkeypatch.setattr(sched_mod, "TICK_S", 0.02)
    server = _server(tmp_path)
    grp = groups.create(server.routines_home, name="Chain",
                        members=[{"slug": "first"}],
                        cron="0 7 * * *", tz="UTC")
    fr = FakeRunner()
    sched = Scheduler(server, fr, EventBus())
    task = asyncio.create_task(sched.run_forever())
    await asyncio.sleep(0.05)
    sched.group_next_fires[grp["id"]] = datetime.now(UTC) - timedelta(seconds=1)
    assert await _wait_for(lambda: ("first", "group") in fr.fired)
    # due AGAIN while member 0 is still mid-flight → the fire is refused and logged
    p = server.routines_home / ".control" / "health-events.jsonl"
    sched.group_next_fires[grp["id"]] = datetime.now(UTC) - timedelta(seconds=1)
    assert await _wait_for(lambda: p.exists() and "group_fire_refused" in p.read_text())
    task.cancel()
    ev = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
          if "group_fire_refused" in x][-1]
    assert ev["routine"] == grp["id"] and ev["run_id"] == ""
    assert "still in flight" in ev["detail"]
