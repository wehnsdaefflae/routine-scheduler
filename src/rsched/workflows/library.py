"""Workflow library access: flat markdown files in a git repo, catalog derived live."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .. import frontmatter


def workflows_dir(home: Path) -> Path:
    return home / "workflows"


def fragments_dir(home: Path) -> Path:
    return home / "fragments"


def proposals_dir(home: Path) -> Path:
    return home / "proposals"


def list_workflows(home: Path) -> list[dict]:
    out = []
    d = workflows_dir(home)
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.md")):
        meta, _ = frontmatter.load(path)
        out.append({"slug": path.stem, "file": path.name,
                    "name": meta.get("name", path.stem),
                    "description": meta.get("description", ""),
                    "when_to_use": str(meta.get("when_to_use", "")).strip(),
                    "version": meta.get("version", 0),
                    "status": meta.get("status", "draft"),
                    "includes": meta.get("includes") or [],
                    "tags": meta.get("tags") or []})
    return out


def list_fragments(home: Path) -> list[str]:
    d = fragments_dir(home)
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def read_workflow(home: Path, slug: str) -> tuple[dict, str, str]:
    """(meta, body, raw). Raises FileNotFoundError."""
    path = workflows_dir(home) / f"{slug}.md"
    raw = path.read_text(encoding="utf-8")
    meta, body = frontmatter.parse(raw)
    return meta, body, raw


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
