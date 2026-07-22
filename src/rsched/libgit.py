"""Serialized commits to the ONE shared library repo.

Every writer of the library repo — engine `write_util`/`remove_util` runs, web
playbook/workflow/util/doc edits, on-demand workflow generation — funnels through
`commit()`. Two things make concurrent writes safe (a run may `write_util` while another
run, or a Library-tab edit, commits the same repo):

- a per-repo file lock (`paths.repo_lock_path`), so two writers never collide on git's
  `index.lock`; and
- a SCOPED stage (`git add -A -- <paths>`), so one writer's `git add` can never sweep a
  sibling's not-yet-committed file into the wrong commit. Callers that changed known paths
  MUST pass them; the unscoped `git add -A` fallback stays only for whole-tree operations.

Best-effort, exactly like the per-module helpers it replaces: git/OS errors return False.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from .paths import file_lock, repo_lock_path

_TIMEOUT = 30


def _git(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(home), *args], capture_output=True,
                          text=True, timeout=_TIMEOUT, check=False)


def commit(home: Path, message: str, *, paths: Sequence[str] | None = None) -> bool:
    """Stage (scoped to `paths` when given) and commit under the repo lock. Returns True on
    a successful commit, False on nothing-to-commit or any git/OS error.
    """
    home = Path(home)
    try:
        with file_lock(repo_lock_path(home)):
            if paths:
                _git(home, "add", "-A", "--", *paths)
            else:
                _git(home, "add", "-A")
            return _git(home, "commit", "-qm", message).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
