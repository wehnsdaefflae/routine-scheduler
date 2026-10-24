"""Workflow library access: flat Python pattern files in a git repo, catalog derived live.

Reads only. A WRITER commits for itself — `libgit.commit(home, message, routines_home=…,
paths=…)` (C1.10): this module held a `git_commit` that did nothing but return that call, and a
pass-through in front of the lock-aware, SIGTERM-first, failure-filing primitive is a name a
reader must follow and the place a divergent default would hide. `head_commit` and `git_log`
stay because they ANSWER something about the library the caller cannot get from `libgit` alone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .. import libgit


def workflows_dir(home: Path) -> Path:
    return home / "workflows"


def rules_dir(home: Path) -> Path:
    return home / "rules"


def permissions_dir(home: Path) -> Path:
    return home / "permissions"


def _workflow_paths(home: Path) -> list[Path]:
    """Every workflow file, one `<slug>.py` pattern per slug."""
    d = workflows_dir(home)
    return sorted(d.glob("*.py")) if d.is_dir() else []


def list_workflows(home: Path) -> list[dict]:
    out = []
    for path in _workflow_paths(home):
        meta = _read_meta(path)
        out.append({"slug": path.stem, "file": path.name,
                    "name": meta.get("name", path.stem),
                    "description": meta.get("description", ""),
                    "when_to_use": str(meta.get("when_to_use", "")).strip(),
                    "version": meta.get("version", 0),
                    "includes": meta.get("includes") or [],
                    "tags": meta.get("tags") or []})
    return out


def _read_meta(path: Path) -> dict:
    """Parse a workflow pattern's META (statically; unparseable files list with empty meta)."""
    from .pyworkflow import parse_py
    try:
        return parse_py(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError):
        return {}


def list_rules(home: Path) -> list[str]:
    d = rules_dir(home)
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def read_workflow(home: Path, slug: str) -> tuple[dict, str]:
    """(meta, raw source) for `<slug>.py` — the whole file is the pattern.
    Raises FileNotFoundError.
    """
    from .pyworkflow import parse_py
    raw = (workflows_dir(home) / f"{slug}.py").read_text(encoding="utf-8")
    return parse_py(raw), raw


def head_commit(home: Path) -> str:
    """The library's short HEAD — provenance, best-effort: "" when git cannot say, whether
    it fails, is missing, or runs out of time (libgit.git raises TimeoutExpired then).
    """
    try:
        r = libgit.git(home, "rev-parse", "--short", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git_log(home: Path, rel_path: str | None = None, limit: int = 20) -> list[dict]:
    from ..libgit import git_log as _git_log
    return _git_log(home, rel_path, limit)
