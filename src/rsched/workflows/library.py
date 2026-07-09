"""Workflow library access: flat markdown files in a git repo, catalog derived live."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .. import frontmatter


def workflows_dir(home: Path) -> Path:
    return home / "workflows"


def recipe_dir(home: Path, slug: str) -> Path:
    """A recipe is a directory: <workflows>/<slug>/main.md (entry) + steps/<module>.md."""
    return workflows_dir(home) / slug


def main_path(home: Path, slug: str) -> Path:
    return recipe_dir(home, slug) / "main.md"


def list_modules(home: Path, slug: str) -> list[str]:
    steps = recipe_dir(home, slug) / "steps"
    return sorted(p.stem for p in steps.glob("*.md")) if steps.is_dir() else []


def read_module(home: Path, slug: str, module: str) -> str | None:
    p = recipe_dir(home, slug) / "steps" / f"{module}.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def fragments_dir(home: Path) -> Path:
    return home / "fragments"


def proposals_dir(home: Path) -> Path:
    return home / "proposals"


def list_workflows(home: Path) -> list[dict]:
    out = []
    d = workflows_dir(home)
    if not d.is_dir():
        return out
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
        main = sub / "main.md"
        if not main.is_file():
            continue
        meta, _ = frontmatter.load(main)
        out.append({"slug": sub.name, "file": f"{sub.name}/main.md",
                    "name": meta.get("name", sub.name),
                    "description": meta.get("description", ""),
                    "when_to_use": str(meta.get("when_to_use", "")).strip(),
                    "version": meta.get("version", 0),
                    "status": meta.get("status", "draft"),
                    "includes": meta.get("includes") or [],
                    "modules": list_modules(home, sub.name),
                    "tags": meta.get("tags") or []})
    return out


def list_fragments(home: Path) -> list[str]:
    d = fragments_dir(home)
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def read_workflow(home: Path, slug: str) -> tuple[dict, str, str]:
    """(meta, body, raw) of a recipe's main.md. Raises FileNotFoundError."""
    path = main_path(home, slug)
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
