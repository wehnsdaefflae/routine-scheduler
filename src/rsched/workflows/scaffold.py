"""Create a routine directory: materialized workflow, routine.yaml, seeds, its own git repo
with the best-effort auto-push hook."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from ..config import DEFAULT_BUDGETS, DEFAULT_SELF, ServerConfig
from ..ids import is_slug
from .adapt import materialize

GITIGNORE = "runs/\ninbox/\nquestions/\n"

POST_COMMIT_HOOK = """#!/usr/bin/env bash
# rsched auto-backup — push every commit to origin (best-effort, never blocks the commit).
branch="$(git symbolic-ref --short HEAD 2>/dev/null)" || exit 0
git remote get-url origin >/dev/null 2>&1 || exit 0
out="$(timeout 20 git push --quiet origin "$branch" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  printf '[rsched backup] push to origin failed (exit %d)…\\n%s\\n' "$rc" "$out" >&2
fi
exit 0
"""


def scaffold(server: ServerConfig, *, slug: str, name: str, instruction: str,
             workflow_slug: str, cron: str = "", tz: str = "Europe/Berlin",
             params: dict | None = None, budgets: dict | None = None,
             self_flags: dict | None = None, shell_allowlist: list[str] | None = None,
             fs_read_roots: list[str] | None = None,
             fs_write_roots: list[str] | None = None,
             playbook: dict[str, str] | None = None, enabled: bool = True) -> Path:
    """Create ~/routines/<slug>. Raises ValueError on a bad/taken slug, KeyError on
    missing workflow params."""
    if not is_slug(slug):
        raise ValueError(f"slug {slug!r} is not kebab-case")
    routine_dir = server.routines_home / slug
    if routine_dir.exists():
        raise ValueError(f"routine dir {routine_dir} already exists")

    self_flags = {**DEFAULT_SELF, **(self_flags or {})}
    content, provenance = materialize(server.library_home, workflow_slug,
                                      params=params, self_flags=self_flags)

    for sub in ("state", "playbook", "inbox"):
        (routine_dir / sub).mkdir(parents=True)
    # Purpose-specific step files (the routine's on-demand playbook), if the wizard split them out.
    for fname, fcontent in (playbook or {}).items():
        safe = fname if fname.endswith(".md") else f"{fname}.md"
        (routine_dir / "playbook" / Path(safe).name).write_text(fcontent, encoding="utf-8")
    (routine_dir / "workflow.md").write_text(content, encoding="utf-8")
    (routine_dir / "instruction.md").write_text(instruction.rstrip() + "\n", encoding="utf-8")
    (routine_dir / "LEDGER.md").write_text(
        f"# LEDGER — {name}\n\n### seed — routine scaffolded from workflow "
        f"'{workflow_slug}' v{provenance.get('version')} @ {provenance.get('commit')}\n",
        encoding="utf-8")
    (routine_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    cfg = {
        "name": name,
        "slug": slug,
        "enabled": enabled,
        "schedule": {"cron": cron, "tz": tz, "catchup": "skip"},
        "workflow": {"library_slug": workflow_slug,
                     "library_commit": provenance.get("commit", "")},
        "budgets": {**DEFAULT_BUDGETS, **(budgets or {})},
        "self": self_flags,
        "notifications": "ui",
        "retention": {"keep_runs": 30},
    }
    if shell_allowlist:
        cfg["shell_allowlist"] = shell_allowlist
    if fs_read_roots:
        cfg["fs_read_roots"] = [_tilde(p) for p in fs_read_roots]
    if fs_write_roots:
        cfg["fs_write_roots"] = [_tilde(p) for p in fs_write_roots]
    (routine_dir / "routine.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

    _git_init(routine_dir, f"scaffold {slug} from workflow {workflow_slug}")
    return routine_dir


def _tilde(path: str) -> str:
    """Collapse $HOME → ~ so an absolute path never embeds the account/home-dir name."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


# Neutral identity for managed repos — the user's real name never authors a commit.
GIT_IDENTITY = (("user.name", "routine-scheduler"),
                ("user.email", "noreply@routine-scheduler.local"))


def init_repo(repo_dir: Path, message: str) -> None:
    """git init a managed repo with the neutral identity + best-effort push hook, then
    make the first commit. Shared by routine and util-library scaffolding."""
    try:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir,
                       capture_output=True, timeout=30)
        for key, val in GIT_IDENTITY:
            subprocess.run(["git", "config", key, val], cwd=repo_dir, capture_output=True, timeout=15)
        hook = repo_dir / ".git" / "hooks" / "post-commit"
        hook.write_text(POST_COMMIT_HOOK, encoding="utf-8")
        os.chmod(hook, 0o755)
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo_dir,
                       capture_output=True, timeout=30)
    except OSError:
        pass  # a routine without git still runs; the workflow can init later


def _git_init(routine_dir: Path, message: str) -> None:
    init_repo(routine_dir, message)
