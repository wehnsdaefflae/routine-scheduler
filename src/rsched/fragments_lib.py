"""Fragment library — reusable standards (self-management + how-to-use-utils) that routines
toggle on. A first-class git-backed library like the workflow and util libraries; routines
keep editable copies of their active fragments under <routine>/fragments/.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import frontmatter

FRAGMENT_RE = re.compile(r"^#\s*fragment:\s*(?P<slug>[a-z0-9-]+)\s*(?:—|-)\s*(?P<summary>.+)$", re.M)


def fragment_body(raw: str) -> str:
    """The fragment text as inlined into a prompt — frontmatter (tags, etc.) stripped."""
    return frontmatter.parse(raw)[1]


def ensure_library(home: Path, *, remote: str = "") -> None:
    if home.exists() and (home / ".git").exists():
        return
    home.parent.mkdir(parents=True, exist_ok=True)
    if remote and not home.exists():
        r = subprocess.run(["git", "clone", "--quiet", remote, str(home)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            _configure(home)
            return
    home.mkdir(parents=True, exist_ok=True)
    _git(home, "init", "-q", "-b", "main")
    _configure(home)
    if remote:
        _git(home, "remote", "add", "origin", remote)


def _configure(home: Path) -> None:
    _git(home, "config", "user.name", "routine-scheduler")
    _git(home, "config", "user.email", "noreply@routine-scheduler.local")


def _git(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(home), *args], capture_output=True, text=True, timeout=30)


def list_fragments(home: Path) -> list[dict]:
    if not home.is_dir():
        return []
    out = []
    for path in sorted(home.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, _ = frontmatter.parse(text)
        m = FRAGMENT_RE.search(text)
        out.append({"slug": path.stem,
                    "summary": (m.group("summary").strip() if m else ""),
                    "title": _title(path.stem, m),
                    "tags": meta.get("tags") or []})
    return out


def _title(slug: str, m) -> str:
    # a short human label, e.g. "self-audit" → "Self audit"
    return slug.replace("-", " ").replace("_", " ").capitalize()


def slugs(home: Path) -> list[str]:
    return [f["slug"] for f in list_fragments(home)]


def read_fragment(home: Path, slug: str) -> str | None:
    p = home / f"{slug}.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_fragment(home: Path, slug: str, content: str) -> None:
    (home / f"{slug}.md").write_text(content, encoding="utf-8")


def git_commit(home: Path, message: str) -> bool:
    try:
        _git(home, "add", "-A")
        return _git(home, "commit", "-qm", message).returncode == 0
    except OSError:
        return False


def git_log(home: Path, rel_path: str | None = None, limit: int = 20) -> list[dict]:
    cmd = ["git", "-C", str(home), "log", f"-{limit}", "--format=%h%x09%ad%x09%s", "--date=short"]
    if rel_path:
        cmd += ["--", rel_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except OSError:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"commit": parts[0], "date": parts[1], "subject": parts[2]})
    return out
