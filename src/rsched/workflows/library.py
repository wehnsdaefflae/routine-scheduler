"""Workflow library access: flat Python pattern files in a git repo, catalog derived live."""

from __future__ import annotations

import subprocess
from pathlib import Path


def workflows_dir(home: Path) -> Path:
    return home / "workflows"


def fragments_dir(home: Path) -> Path:
    return home / "fragments"


def proposals_dir(home: Path) -> Path:
    return home / "proposals"


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
                    "status": meta.get("status", "draft"),
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


def list_fragments(home: Path) -> list[str]:
    d = fragments_dir(home)
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def read_workflow(home: Path, slug: str) -> tuple[dict, str, str]:
    """(meta, body, raw) for `<slug>.py`. body==raw==the source — the whole file is the pattern.
    Raises FileNotFoundError."""
    from .pyworkflow import parse_py
    raw = (workflows_dir(home) / f"{slug}.py").read_text(encoding="utf-8")
    return parse_py(raw), raw, raw


def read_fragment(home: Path, slug: str) -> str:
    return (fragments_dir(home) / f"{slug}.md").read_text(encoding="utf-8")


def head_commit(home: Path) -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=home,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except OSError:
        return ""


def git_commit(home: Path, message: str) -> bool:
    try:
        subprocess.run(["git", "add", "-A"], cwd=home, capture_output=True, timeout=30)
        r = subprocess.run(["git", "commit", "-qm", message], cwd=home,
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except OSError:
        return False


def git_log(home: Path, rel_path: str | None = None, limit: int = 20) -> list[dict]:
    cmd = ["git", "log", f"-{limit}", "--format=%h%x09%ad%x09%s", "--date=short"]
    if rel_path:
        cmd += ["--", rel_path]
    try:
        r = subprocess.run(cmd, cwd=home, capture_output=True, text=True, timeout=15)
    except OSError:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"commit": parts[0], "date": parts[1], "subject": parts[2]})
    return out


def list_proposals(home: Path) -> list[dict]:
    d = proposals_dir(home)
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.md")):
        decision_file = path.with_suffix(".decision.json")
        from ..paths import read_json

        decision = read_json(decision_file)
        out.append({"id": path.stem, "file": path.name,
                    "content": path.read_text(encoding="utf-8"),
                    "decision": decision if isinstance(decision, dict) else None})
    return out
