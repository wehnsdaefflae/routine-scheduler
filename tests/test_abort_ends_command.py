"""A util, script or `shell` command ends WITH its run when the run is aborted.

The defect (found 2026-10-01): the daemon aborts a run by SIGTERMing the engine's process group
and SIGKILLing it `runner_state.KILL_GRACE_S` later. The engine's SIGTERM handler only raises an
abort flag, read at turn boundaries. A jailed command runs in a session of its own, so neither
signal reaches it — and `run_jailed`'s `proc.wait(timeout=...)` did not look at the flag. The
engine was SIGKILLed while it waited; the command ran on with no deadline at all, since the
engine had been its clock. Reproduced against a real engine run on the production host: a
`shell` `sleep` outlived its aborted run with PPID 1 while status.json stayed `running`.

Pinned here: the wait asks the run's own abort check (`RunContext.aborted`, installed by the
loop — the SIGTERM flag OR a child run's kill event) and ends the group through
`procgroup.terminate`; the observation says so in words and in a field; an ended call is
neither a util failure (no repair route) nor a reliability tick; and a run that is already
stopping starts nothing.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from conftest import finish
from helpers import server_for
from rsched import sandbox, scripts, shellrun, utils_lib, utils_run
from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.control import request_abort
from rsched.engine.run_context import RunContext
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import Transcript, read_events
from rsched.readmodels import util_stats

PATH_ENV = {"PATH": "/usr/bin:/bin"}
TS = "20261001-120000"
#: A command that backgrounds a member — the shape `uv run` gives every util — and names it.
GROUP = "sleep 60 & echo $! > member.tmp && mv member.tmp member; wait"
ENDED = re.compile(r"was ended by the run's abort after \d+s \(process group terminated\)")


def _until(check, limit: float = 20.0) -> None:
    deadline = time.monotonic() + limit
    while not check():
        assert time.monotonic() < deadline, "the command never got going"
        time.sleep(0.02)


def _alive(pid: int) -> bool:
    """Whether `pid` still runs — a zombie (exited, not yet collected by its init) does not."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return not stat.rsplit(") ", 1)[1].startswith("Z")


def _gone(pid: int, limit: float = 10.0) -> bool:
    deadline = time.monotonic() + limit
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


# -- the runner --------------------------------------------------------------------------

def test_the_runs_abort_ends_the_command_and_its_whole_group(tmp_path):
    member = tmp_path / "member"
    started = time.monotonic()
    res = utils_run.run_jailed(["bash", "-c", f"echo started; {GROUP}"], env=PATH_ENV,
                               cwd=tmp_path, timeout=30, label="the command",
                               aborted=member.exists)
    assert time.monotonic() - started < 15          # the abort ended it, not the deadline
    assert res.aborted is True
    assert res.timed_out is False
    assert res.exit_code == utils_run.ABORT_EXIT == 130
    assert "started" in res.stdout                  # what it printed before the end survives
    assert ENDED.search(res.stderr)
    assert "the command was ended by the run's abort" in res.stderr
    assert _gone(int(member.read_text(encoding="utf-8")))


def test_a_run_already_stopping_starts_nothing(tmp_path):
    res = utils_run.run_jailed(["bash", "-c", "touch ran"], env=PATH_ENV, cwd=tmp_path,
                               timeout=30, label="util 'x'", aborted=lambda: True)
    assert not (tmp_path / "ran").exists()
    assert res.aborted is True
    assert res.exit_code == utils_run.ABORT_EXIT
    assert res.stderr == "util 'x' was not started: the run was aborted"


def test_an_exception_out_of_the_wait_ends_the_group_before_it_propagates(tmp_path):
    """A KeyboardInterrupt where no handler maps SIGINT to the abort flag left the group with
    no deadline, exactly as the abort did. `libgit.git` already ended its group on any
    BaseException; the jailed runner now does too.
    """
    member = tmp_path / "member"

    def interrupted() -> bool:
        if member.exists():
            raise KeyboardInterrupt
        return False

    with pytest.raises(KeyboardInterrupt):
        utils_run.run_jailed(["bash", "-c", GROUP], env=PATH_ENV, cwd=tmp_path, timeout=30,
                             aborted=interrupted)
    assert _gone(int(member.read_text(encoding="utf-8")))


def test_without_an_abort_check_the_command_runs_to_its_end(tmp_path):
    """Callers outside any run (the CLI, a selftest from the web, notify) pass none."""
    res = utils_run.run_jailed(["bash", "-c", "sleep 0.3; echo done"], env=PATH_ENV,
                               cwd=tmp_path, timeout=30)
    assert res.aborted is False
    assert res.exit_code == 0
    assert res.stdout.strip() == "done"


@pytest.mark.parametrize("kind", ["script", "shell"])
def test_both_riders_report_an_abort_with_what_was_printed(tmp_path, make_routine, kind):
    routine = make_routine(slug=f"abort-{kind}")
    policy = sandbox.SandboxPolicy(mode="off", own_dir=routine)
    printed = routine / "printed"         # the abort lands once the print has, never before it
    if kind == "shell":
        res = shellrun.run_shell("echo early; touch printed; sleep 60", policy=policy,
                                 libraries_home=tmp_path / "lib", cwd=routine, timeout=30,
                                 aborted=printed.exists)
        assert res["aborted"] is True
        assert res["timed_out"] is False
        code, out, err = res["exit"], res["stdout"], res["stderr"]
    else:
        (routine / "scripts").mkdir(exist_ok=True)
        (routine / "scripts" / "slow.py").write_text(
            '"""slow — sleeps."""\nimport pathlib, time\nprint("early", flush=True)\n'
            'pathlib.Path("printed").touch()\ntime.sleep(60)\n', encoding="utf-8")
        # the venv is the BUILD phase, not this call — point the runner at the interpreter
        # running the tests instead of installing one
        (routine / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (routine / ".venv" / "bin" / "python").symlink_to(sys.executable)
        code, out, err = scripts.run_script(routine, "slow", [], policy=policy,
                                            libraries_home=tmp_path / "lib", timeout=30,
                                            aborted=printed.exists)
    assert code == utils_run.ABORT_EXIT
    assert "early" in out
    assert ENDED.search(err)


def test_a_venv_build_the_abort_ended_reports_the_abort(tmp_path, make_routine):
    """The build before a script is a jailed call too; ended by the abort, it reports the
    abort's code and the runner's own words — not a failed dependency install.
    """
    routine = make_routine(slug="abort-build")
    (routine / "scripts").mkdir()
    (routine / "scripts" / "fresh.py").write_text('"""fresh — never runs."""\n',
                                                  encoding="utf-8")
    code, out, err = scripts.run_script(routine, "fresh", [],
                                        policy=sandbox.SandboxPolicy(mode="off", own_dir=routine),
                                        libraries_home=tmp_path / "lib", aborted=lambda: True)
    assert code == utils_run.ABORT_EXIT
    assert out == ""
    assert err == "venv setup (venv) was not started: the run was aborted"


# -- the engine ---------------------------------------------------------------------------

def _server(routine_dir: Path) -> ServerConfig:
    # the jail's inputs are pinned elsewhere; this exercises the process
    return server_for(routine_dir, sandbox="off")


def test_an_abort_ends_the_shell_command_in_flight(make_routine, scripted):
    """The defect end to end, minus the daemon: the abort arrives while the turn's command is
    running. The run finishes `aborted` with its own close-out — the observation recorded, a
    resumed run able to read why the call has no result — and nothing it started is left.
    """
    d = make_routine(slug="aborter")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["capabilities"] = {"actions": ["shell"]}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    scripted([{"say": "Sleeping.", "kind": "shell", "command": GROUP, "timeout_s": 40},
              finish(summary="never reached")])
    member = d / "member"

    def abort_once_running() -> None:
        deadline = time.monotonic() + 20
        while not member.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        request_abort()      # what cli.cmd_engine_run's SIGTERM handler does

    aborter = threading.Thread(target=abort_once_running, daemon=True)
    aborter.start()
    started = time.monotonic()
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    aborter.join(timeout=25)   # the flag moves before the fixture resets it, never after
    assert status == "aborted"
    assert time.monotonic() - started < 20            # the shell's 40 s deadline never mattered
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = next(e["payload"] for e in events if e["type"] == "observation")
    assert obs["kind"] == "shell"
    assert obs["aborted"] is True
    assert obs["exit"] == utils_run.ABORT_EXIT
    assert ENDED.search(obs["stderr"])
    assert events[-1]["type"] == "finish"
    assert events[-1]["payload"]["status"] == "aborted"
    assert _gone(int(member.read_text(encoding="utf-8")))


def test_the_loop_installs_its_own_check_a_childs_kill_included(make_routine, monkeypatch):
    """`ctx.aborted` is the loop's `_aborted`: a child run is stopped by its OWN event (a
    parent's `kill`, or the parent finishing) without the process-wide flag ever moving, and
    its jailed calls must hear that too.
    """
    from rsched.engine import control
    from rsched.engine.loop import EngineLoop

    monkeypatch.setitem(control._ABORT, "flag", False)

    d = make_routine(slug="childish")
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=_server(d), registry=None, run_ts=TS, run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    assert ctx.aborted() is False                     # no loop drives it yet: never stopping
    kill = threading.Event()
    EngineLoop(ctx, "## Run flow", "instr", abort_event=kill)
    assert ctx.aborted() is False
    kill.set()
    assert ctx.aborted() is True


# -- what the observation and the stats say ------------------------------------------------

SLOW_UTIL = '''# /// script
# dependencies = []
# ///
"""slowpoke — sleeps (test fixture).

usage: gu slowpoke
"""
import time
time.sleep(60)
'''


def _util_ctx(home: Path, ticks: list):
    from types import SimpleNamespace
    return SimpleNamespace(server=SimpleNamespace(libraries_home=home, sandbox="off",
                                                 routine_token=""),
                           routine=SimpleNamespace(slug="demo", dir=home, fs_read_roots=[],
                                                   fs_write_roots=[], connections={},
                                                   machines=[]),
                           grants=None, read_roots=list, write_roots=list,
                           granted_now=frozenset(), grant_args={},
                           count_util=lambda *a: ticks.append(a),
                           note_compression=lambda metrics: None, aborted=lambda: True)


def test_an_aborted_util_call_is_neither_a_failure_nor_a_tick(tmp_path):
    """Exit 130 is not the util failing: no `[hint]` sending a resumed run off to repair a
    util that is fine, no `[usage]`, and no reliability tick on the Stats tab.
    """
    from rsched.engine.executor import do_util
    from rsched.engine.observations import format_observation

    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "slowpoke", SLOW_UTIL)
    ticks: list = []
    obs = do_util({"kind": "util", "name": "slowpoke", "args": []}, _util_ctx(tmp_path, ticks))
    assert obs["exit"] == utils_run.ABORT_EXIT
    assert obs["aborted"] is True
    assert "hint" not in obs
    assert "usage" not in obs
    assert ticks == []
    text = format_observation(obs)
    assert text.startswith("OBSERVATION (util slowpoke, exit 130):")
    assert "util 'slowpoke' was not started: the run was aborted" in text
    assert "[hint]" not in text


def test_the_stats_backfill_skips_an_aborted_call_too(tmp_path):
    path = tmp_path / "transcript.jsonl"
    transcript = Transcript(path)
    transcript.event("observation", {"kind": "util", "name": "slowpoke", "exit": 130,
                                     "aborted": True})
    transcript.event("observation", {"kind": "util", "name": "slowpoke", "exit": 0})
    transcript.close()
    counts = util_stats._scan_transcript(path)["slowpoke"]["counts"]
    assert counts["ok"] == 1
    assert counts["error"] == 0
