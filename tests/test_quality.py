"""The ruff + mypy gates, run as tests — so the ONE gate the engine actually runs covers them.

CLAUDE.md requires `ruff check` and `mypy` green on the FULL repo in every commit and states
"pre-commit enforces both". But on the deployment the daemon commits PROGRAMMATICALLY (git
hooks bypassed) and pre-commit is not installed, while the self-audit routine's only hard gate
is `util pytest-run`. So a ruff/mypy regression in a file no run touched sailed through
unnoticed for four releases — the Jul-2026 toolchain bump (uv.lock: ruff 0.15.21 / mypy 2.3.0)
turned the tree red and 0.72–0.76 kept committing over it (found by the F97 external audit,
2026-07-20). Folding the two gates into pytest means red can never be committed silently again:
the routine reverts on a red suite, and the checks run over the live tree's pending edits too.
"""

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

    `src` and `tests` are scanned TOGETHER on purpose: a src symbol exercised only by a test is
    not dead, and scanning src alone reports three such false positives. Config, including the
    framework entry points that are called by decorator rather than by name, is in pyproject.
    """
    _gate("vulture", "src", "tests", "--min-confidence", "60")


def test_the_gate_runs_when_its_tool_is_only_beside_the_interpreter(monkeypatch):
    """Measured in the running container: PATH is `/usr/local/bin:…:/bin` and carries none of
    the three tools, because the image sets no PATH and `/opt/rsched-venv/bin` is not on it. Only
    `uv run` prepended it — so every invocation by absolute interpreter, the routine's own
    worktree gate included, found nothing."""
    monkeypatch.setenv("PATH", "")
    try:
        _gate("ruff", "--version")
    except pytest.skip.Exception as exc:
        pytest.fail(f"the gate skipped instead of running: {exc}")


def test_a_missing_tool_fails_the_gate_instead_of_skipping(monkeypatch):
    """A skip reads as a pass in every consumer of this suite."""
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")
    with pytest.raises(pytest.fail.Exception, match="the commit gate"):
        _gate("ruff", "--version")
