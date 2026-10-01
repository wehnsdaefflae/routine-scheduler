"""Ending a process group the way its members can clean up after (`procgroup.terminate`).

The 2026-09-30 incident in miniature: git deletes its `index.lock` only in its SIGTERM handler,
so a deadline that SIGKILLs it leaves the lock behind. What the helper owes both of its callers
(`libgit.git`, `utils_run.run_jailed`): SIGTERM first, a wait that covers the whole GROUP rather
than its leader, and SIGKILL only for what outlives the grace.
"""

from __future__ import annotations

import ast
import subprocess
import time
from pathlib import Path

from rsched import libgit, procgroup, utils_run


def _until(check, limit: float = 10.0) -> None:
    deadline = time.monotonic() + limit
    while not check():
        assert time.monotonic() < deadline, "the child never got ready"
        time.sleep(0.02)


def _group(cmd: str, cwd: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(["bash", "-c", cmd], cwd=cwd, text=True, start_new_session=True)


def test_a_grandchild_finishes_its_cleanup_after_the_leader_is_gone(tmp_path):
    """The shape of a util: the leader (`uv run`) exits within milliseconds of SIGTERM while a
    git below it is still deleting its lock. A wait for the leader alone SIGKILLed that
    grandchild in the middle of its handler; the cleanup must finish.
    """
    (tmp_path / "member.sh").write_text(
        "trap 'sleep 0.5; echo done > cleaned; exit 143' TERM\ntouch ready\nsleep 30 & wait\n",
        encoding="utf-8")
    proc = _group("bash member.sh & wait", tmp_path)
    _until((tmp_path / "ready").exists)
    started = time.monotonic()
    assert procgroup.terminate(proc) is True
    assert (tmp_path / "cleaned").read_text(encoding="utf-8") == "done\n"
    assert time.monotonic() - started < 10       # it ended with the group, not with the grace
    assert proc.returncode == -15                # the leader went on SIGTERM and was reaped


def test_a_group_that_exits_on_sigterm_spends_none_of_the_grace(tmp_path):
    proc = _group("touch ready; sleep 30 & wait", tmp_path)
    _until((tmp_path / "ready").exists)
    started = time.monotonic()
    assert procgroup.terminate(proc) is True
    assert time.monotonic() - started < 5
    assert proc.returncode is not None


def test_what_ignores_sigterm_is_killed_when_the_grace_runs_out(tmp_path, monkeypatch):
    monkeypatch.setattr(procgroup, "TERM_GRACE_S", 0.5)
    proc = _group("trap '' TERM; touch ready; sleep 30", tmp_path)
    _until((tmp_path / "ready").exists)
    started = time.monotonic()
    assert procgroup.terminate(proc) is False
    assert 0.5 <= time.monotonic() - started < 10
    assert proc.returncode == -9


def test_a_leader_that_already_exited_is_reaped_not_waited_for(tmp_path):
    """Until it is collected, an exited leader's zombie still answers `killpg(pgid, 0)` for
    the group — the wait reaps it instead of spending the grace on it.
    """
    proc = _group("exit 3", tmp_path)
    stat = Path(f"/proc/{proc.pid}/stat")
    _until(lambda: stat.read_text(encoding="utf-8").rsplit(") ", 1)[1].startswith("Z"))
    started = time.monotonic()
    assert procgroup.terminate(proc) is True
    assert time.monotonic() - started < 5
    assert proc.returncode == 3


def test_both_runners_end_a_group_through_the_one_helper():
    """Two copies of this drifted once already: `libgit` waited for its group's leader and
    `run_jailed` sent SIGKILL at once. Neither runner signals a group itself any more.
    """
    for module in (libgit, utils_run):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert {"killpg", "SIGKILL", "SIGTERM"}.isdisjoint(names), module.__name__
        assert "terminate" in names, module.__name__
