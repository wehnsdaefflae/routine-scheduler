"""The ruff + mypy gates, run as tests — so the ONE gate the engine actually runs covers them."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _gate(tool: str, *args: str) -> None:
    # PATH first, then the bin directory of the interpreter RUNNING this test. `uv run` prepends
    # the project env's bin, but an invocation by absolute interpreter does not — and the
    # container sets no PATH at all, so `/opt/rsched-venv/bin` is off it. Every run launched as
    # `/opt/rsched-venv/bin/python -m pytest` therefore found no tool; with a skip, all three
    # gates passed by not existing. vulture has no other runner anywhere.
    exe = shutil.which(tool) or shutil.which(tool, path=str(Path(sys.executable).parent))
    if exe is None:
        # NOT a skip. A skip reads as a pass in every consumer of this suite — the routine's
        # test gate, the terminal summary, the junit XML — and this file exists because a red
        # tree once sailed through four releases unseen. A machine without the dev tools has
        # no business committing from this repo.
        pytest.fail(f"{tool} is not on PATH or beside {sys.executable} — the commit gate "
                    f"cannot run, and a gate that cannot run must not report success")
    proc = subprocess.run([exe, *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=600, check=False)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip()[-4000:]
        pytest.fail(f"{tool} {' '.join(args)} failed (exit {proc.returncode}) — the commit gate "
                    f"is RED on the FULL repo:\n{tail}")


def test_ruff_clean():
    _gate("ruff", "check")


def test_mypy_clean():
    _gate("mypy")


def test_vulture_clean():
    """Dead code — symbols defined, exported and referenced by nothing. Ruff (select = ALL)
    already owns unused imports/variables/arguments; what it cannot see is a whole function or
    constant that no longer has a caller, because each file is locally consistent. Adopting this
    found four: a dead ServerConfig property, a duplicated sandbox-mode tuple (two sources of
    truth for one contract), a write-only RunContext field, and a dead wizard helper.
    """
    _gate("vulture", "src", "tests", "--min-confidence", "60")


def test_uv_lock_matches_pyproject():
    """A dependency added to `pyproject.toml` and never re-locked installs NOTHING on the next
    container rebuild, because the rebuild syncs from `uv.lock`. 0.531.4 shipped
    `websockets>=13` as a real dependency — `src/rsched/web/api_screen_view.py` imports it at
    module level — with the lock left at its previous resolution, so the committed tree declared
    a package no rebuilt environment would install; the live venv had it only from a hand
    install. Nothing in the suite compared the two, and neither gate stage reads the lock.
    `--offline` keeps this network-free: the check compares the lock's recorded requirements
    against the manifest's and never resolves anything new.
    """
    _gate("uv", "lock", "--check", "--offline")


def test_the_gate_runs_when_its_tool_is_only_beside_the_interpreter(monkeypatch):
    """Measured in the running container: PATH is `/usr/local/bin:…:/bin` and carries none of
    the three tools, because the image sets no PATH and `/opt/rsched-venv/bin` is not on it. Only
    `uv run` prepended it — so every invocation by absolute interpreter, the routine's own
    worktree gate included, found nothing. A tool the gate cannot find fails it outright (the
    test below), so returning at all is the proof that the interpreter's bin was searched."""
    monkeypatch.setenv("PATH", "")
    _gate("ruff", "--version")


def test_a_missing_tool_fails_the_gate_instead_of_skipping(monkeypatch):
    """A skip reads as a pass in every consumer of this suite."""
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")
    with pytest.raises(pytest.fail.Exception, match="the commit gate"):
        _gate("ruff", "--version")
