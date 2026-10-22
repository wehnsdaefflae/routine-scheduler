"""What the GLOBAL PAUSE (D34) holds: active routine turns, and the run-STARTING halves of the
detached background-task lifecycle (F721) — without clearing a manual pause, and without
holding back the delivery of work that already finished.
"""
from pathlib import Path
from types import SimpleNamespace

from rsched.config import ServerConfig
from rsched.daemon import pause
from rsched.daemon.detached import DetachedManager
from rsched.engine.control import pause_gate
from rsched.paths import atomic_write_json, read_json
from test_detached import DetachedFakeRunner, _owner, _request, _server, _task


def test_global_pause_gate_preserves_manual_pause(tmp_path, monkeypatch):
    """Global resume must not release an independently paused run."""
    server = ServerConfig(routines_home=tmp_path / "routines")
    root = server.routines_home / "worker" / "runs" / "20260913-000000"
    root.mkdir(parents=True)
    atomic_write_json(root / "control.json", {"scheduling_pause": True, "pause": True})
    pause.set_paused(server, True)
    statuses, suspended, polls = [], [], []
    ctx = SimpleNamespace(root_run_dir=root, server=server,
                          write_status=statuses.append, credit_suspended=suspended.append)
    loop = SimpleNamespace(ctx=ctx, _aborted=lambda: False)

    def release(_seconds):
        polls.append(True)
        if len(polls) == 1:
            pause.set_paused(server, False)
        else:
            atomic_write_json(root / "control.json", {"scheduling_pause": True, "pause": False})

    monkeypatch.setattr("rsched.engine.control.time.sleep", release)
    pause_gate(loop, poll_s=0)
    assert len(polls) == 2
    assert statuses == ["paused", "running"]
    assert len(suspended) == 1


def test_global_pause_gate_holds_marked_run_until_resume(tmp_path, monkeypatch):
    """A globally marked routine must stop before its next turn."""
    server = ServerConfig(routines_home=tmp_path / "routines")
    root = server.routines_home / "worker" / "runs" / "20260913-000000"
    root.mkdir(parents=True)
    pause.set_paused(server, True)
    cycle = pause.generation(server)
    atomic_write_json(root / "control.json", {"scheduling_pause": cycle})
    statuses, suspended = [], []
    ctx = SimpleNamespace(root_run_dir=root, server=server,
                          write_status=statuses.append, credit_suspended=suspended.append)
    loop = SimpleNamespace(ctx=ctx, _aborted=lambda: False)
    monkeypatch.setattr("rsched.engine.control.time.sleep", lambda _: pause.set_paused(server, False))
    pause_gate(loop, poll_s=0)
    assert statuses == ["paused", "running"]
    assert len(suspended) == 1
    assert read_json(root / "control.json") == {"scheduling_pause": cycle}
    # A later pause must not revive this run's old hold (manual terminal resume).
    pause.set_paused(server, True)
    assert pause.generation(server) != cycle
    statuses.clear()
    pause_gate(loop, poll_s=0)
    assert statuses == []


# -- detached background tasks (F721) -------------------------------------------------------
# The pause's promise is that the scheduler starts NOTHING on its own. `detached.tick` ran
# outside the scheduler's pause guard and starts two kinds of run: an intake `runner.fire` and
# a wake's `resume_terminal`. A woken leg is not held by `pause_gate` either — that gate holds
# only runs MARKED at the pause click, and a fresh resume carries no mark.

DOC = Path(__file__).resolve().parents[1] / "docs" / "background-tasks.md"


async def test_pause_holds_detached_intake(tmp_path):
    """A paused instance fires NO detached task — and consumes no request doing it."""
    server = _server(tmp_path)
    owner = _owner(server)
    fr = DetachedFakeRunner()
    mgr = DetachedManager(server, fr)
    _request(server, "bg-1", owner)
    pause.set_paused(server, True)
    await mgr.tick()
    assert fr.fired == []
    assert (server.background_home / ".requests" / "bg-1.json").exists()  # never consumed unfired
    assert not (server.background_home / "bg-1").exists()                 # nothing materialized
    pause.set_paused(server, False)
    await mgr.tick()
    assert ("bg-1", "detached") in fr.fired                               # deferred, not dropped
    assert not (server.background_home / ".requests" / "bg-1.json").exists()


async def test_boot_reconcile_under_a_pause_starts_nothing(tmp_path):
    """`_boot` reconciles BEFORE the pause-checking `boot_catchup`, so a restart under a pause
    fired every undelivered task. The hold lives in the manager, so boot inherits it."""
    server = _server(tmp_path)
    owner = _owner(server)
    _task(server, "bg-1", owner, summary="done post-restart")
    _request(server, "bg-2", owner)
    fr = DetachedFakeRunner()
    pause.set_paused(server, True)
    await DetachedManager(server, fr).reconcile()
    assert fr.fired == [] and fr.resumed == []
    assert list((owner / "inbox").glob("msg-bg-*.json"))   # the finished task is still handed back


async def test_pause_delivers_the_finished_task_but_holds_the_wake(tmp_path):
    """The decision F721 left open: DELIVERY proceeds (it starts nothing and the message is
    durable), the WAKE does not (it starts a run leg). Nothing is lost — `wake` is
    state-driven, so the first tick after the resume wakes the owner."""
    server = _server(tmp_path)
    owner = _owner(server)
    task = _task(server, "bg-1", owner, summary="found 42 rows", artifact=True)
    fr = DetachedFakeRunner()
    mgr = DetachedManager(server, fr)
    pause.set_paused(server, True)
    await mgr.tick()
    msgs = list((owner / "inbox").glob("msg-bg-*.json"))
    assert len(msgs) == 1 and read_json(msgs[0])["via"] == "background"
    assert (owner / "artifacts" / "from-bg-bg-1" / "report.md").exists()
    assert (task / "delivered.json").exists()
    assert read_json(owner / "state" / "background.json")[0]["delivered"] is True
    assert fr.resumed == []                                # a resume is a run leg: held
    pause.set_paused(server, False)
    await mgr.tick()
    assert fr.resumed and fr.resumed[0][0] == "c-1" and fr.resumed[0][2] == "detached"


def test_pause_holds_the_reaps_post_finish_wake(tmp_path, monkeypatch):
    """The reap's stranded-message wake is an AUTOMATIC run leg, so a pause holds it.

    F721 gave the pause the two detached phases that start a run, and verified the general
    fact behind it: `runner.fire_blocker` does not test the pause, and
    `engine/control.pause_gate` holds only runs MARKED at the pause click — a freshly resumed
    leg carries no mark. `reap_resume.resume_for_stranded` is the same shape and was missed:
    neither a click nor a schedule, and it spawns an engine process. (F746 moved it out of
    `runner_reap` into `reap_resume`, beside the restart re-open that obeys the same hold.)

    It is HELD, not dropped: the message stays durable in the owner's inbox, so the first wake
    after the resume makes it good. The sigkill retry beside it is deliberately NOT held — that
    one fires once, when the process dies, and holding it would consume the single automatic
    recovery a kernel-killed run is owed.
    """
    from types import SimpleNamespace

    from rsched.daemon import pause, reap_resume

    server = SimpleNamespace(routines_home=tmp_path)
    spawned: list[object] = []
    runner = SimpleNamespace(server=server, spawn=spawned.append,
                             resume_terminal=lambda *a, **k: None)
    cfg = SimpleNamespace(slug="uir", dir=tmp_path / "uir")

    reap_resume.resume_for_stranded(runner, cfg)
    assert len(spawned) == 1, "unpaused: the wake is spawned"
    for coro in spawned:
        coro.close()                       # never awaited in this test

    spawned.clear()
    pause.set_paused(server, True)
    assert pause.paused(server)
    reap_resume.resume_for_stranded(runner, cfg)
    assert spawned == [], "paused: no run leg may be started"

    pause.set_paused(server, False)
    reap_resume.resume_for_stranded(runner, cfg)
    assert len(spawned) == 1, "resumed: the wake happens after the pause lifts"
    for coro in spawned:
        coro.close()


def test_pause_scope_is_documented_where_both_readers_look():
    """`daemon/pause.py` ENUMERATES what the pause holds, so it has to stay true; and
    docs/background-tasks.md is the page a reader of this feature reaches. F721 was silent on
    both — the doc contained no 'paus' at all."""
    assert "detached" in (pause.__doc__ or "").lower()
    assert "paus" in (DetachedManager.tick.__doc__ or "").lower()
    doc = DOC.read_text(encoding="utf-8")
    head = "## What the global pause holds"
    assert head in doc, f"docs/background-tasks.md must state the pause contract ({head})"
    section = doc.split(head, 1)[1].split("\n## ", 1)[0]
    for phase in ("intake", "wake", "deliver"):
        assert phase in section, f"the pause section does not say what happens to {phase}"
