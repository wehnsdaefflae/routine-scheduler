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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _gate(tool: str, *args: str) -> None:
    exe = shutil.which(tool)
    if exe is None:  # a minimal env without dev tools — the real commit gate always has them
        pytest.skip(f"{tool} not installed")
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
