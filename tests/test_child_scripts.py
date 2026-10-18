"""A CHILD run reaches the routine's own `scripts/`.

A child's own dir is its workspace, `runs/<ts>/sub/<n>/`, and script lookup asked only that
dir (and an open job's workspace), so a child found no script at all. Holding no capability,
it holds no `shell` either: a child briefed to build and test one item of a parallel batch
could edit files but could not run the routine's test gate or any other tooling it keeps
(the builder's parallel builds, 2026-10-18). The routine's scripts run inside the child's own
jail, whose roots already extend to the routine's dir (F185) — so this is reach, not authority.
"""

from __future__ import annotations

from types import SimpleNamespace

from conftest import finish, subtask, wait_
from helpers import run_context, server_config
from rsched.config import ServerConfig
from rsched.engine.childrun import _sub_routine
from rsched.engine.executor import available_scripts, script_home
from rsched.engine.reach import routine_root
from rsched.engine.run_context import RunContext
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import Transcript, read_events

TS = "20261018-030000"
SCRIPT = '"""gate — a test tool the routine keeps.\n\nnet: none\nfs: roots\n"""\nprint("ok")\n'


def _routine(make_routine):
    d = make_routine(slug="builder")
    (d / "scripts").mkdir()
    (d / "scripts" / "gate.py").write_text(SCRIPT, encoding="utf-8")
    (d / "runs" / TS).mkdir(parents=True)
    return d


def _child(parent: RunContext, n: int = 1) -> RunContext:
    """A child exactly as `childrun.build_child` wires one (tests/test_fileops_reach.py)."""
    sub = parent.run_dir / "sub" / str(n)
    (sub / "state").mkdir(parents=True)
    return RunContext(routine=_sub_routine(parent.routine, sub, SimpleNamespace(name="m")),
                      server=parent.server, registry=None, run_ts=parent.run_ts, run_dir=sub,
                      transcript=Transcript(sub / "transcript.jsonl"),
                      budgets=parent.budgets, depth=parent.depth + 1)


def _top(d, tmp_path) -> RunContext:
    return run_context(d, TS, server=server_config(libraries_home=tmp_path / "libraries",
                                                   routines_home=d.parent))


def test_a_child_and_a_grandchild_find_the_routines_scripts(make_routine, tmp_path):
    d = _routine(make_routine)
    top = _top(d, tmp_path)
    child = _child(top)
    grandchild = _child(child)
    for ctx in (top, child, grandchild):
        assert routine_root(ctx).resolve() == d.resolve()
        assert script_home(ctx, "gate").resolve() == d.resolve()
        assert "gate" in available_scripts(ctx)
    assert script_home(child, "absent") is None


def test_a_childs_own_script_comes_before_the_routines(make_routine, tmp_path):
    """The child's own dir is still asked first, as a top-level run's is: a script a child
    wrote for itself is the one its call means."""
    d = _routine(make_routine)
    child = _child(_top(d, tmp_path))
    (child.routine.dir / "scripts").mkdir()
    (child.routine.dir / "scripts" / "gate.py").write_text(SCRIPT, encoding="utf-8")
    assert script_home(child, "gate") == child.routine.dir


def test_a_subtask_runs_the_routines_script_from_the_routines_dir(make_routine, scripted,
                                                                  monkeypatch):
    d = _routine(make_routine)
    calls = []
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda rd, name, args, **kw: calls.append((rd, name)) or (0, "ok", ""))
    scripted([
        ("(no previous runs)", subtask("CHILD-A: test the change.", label="item")),
        ("CHILD-A", {"say": "testing", "kind": "script", "name": "gate", "args": []}),
        ("CHILD-A", finish(summary="tested")),
        ("(no previous runs)", wait_(n=1)),
        ("(no previous runs)", finish(summary="collected")),
    ])
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "test-library"
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert calls == [(d, "gate")]                    # found, and run from the routine's home
    events, _ = read_events(run_dir / "sub" / "1" / "transcript.jsonl")
    obs = next(e["payload"] for e in events
               if e["type"] == "observation" and e["payload"].get("kind") == "script")
    assert obs["exit"] == 0 and obs["stdout"] == "ok"
