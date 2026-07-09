"""First-boot bootstrap for a fresh (container) deploy. A host install runs deploy/install.sh; the
container has no install step, so the daemon + Settings do the equivalent: generate a config with a
random token if none exists (a fresh deploy must never serve an OPEN API), and seed a library from
the built-in defaults when the user chooses to create a new repo.
"""
from __future__ import annotations

import logging
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from .paths import config_file

log = logging.getLogger("rsched.bootstrap")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_config() -> bool:
    """Create config.yaml with a random token if it's missing. Returns True if it generated one.
    Without this a fresh deploy has an empty token → auth is disabled → an open API on the LAN."""
    path = config_file()
    if path.exists():
        return False
    token = secrets.token_urlsafe(24)
    example = repo_root() / "config" / "config.example.yaml"
    if example.exists():
        text = re.sub(r'token:\s*"change-me".*', f'token: "{token}"', example.read_text(encoding="utf-8"))
    else:
        text = f'bind: 127.0.0.1\nport: 8321\ntoken: "{token}"\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.warning("first boot: generated %s with a fresh access token", path)
    return True


def _git(home: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(home), *args], capture_output=True)


def install_push_hook(home: Path) -> None:
    """Best-effort auto-push-on-commit hook, so generated library changes sync to the remote."""
    src = repo_root() / "deploy" / "post-commit"
    if src.exists() and (home / ".git").is_dir():
        dst = home / ".git" / "hooks" / "post-commit"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        dst.chmod(0o755)


def seed_routines(routines_home: Path) -> int:
    """On a fresh install (no routines yet), install the bundled meta routines — disabled, so they
    show up under the 'meta' tag for the user to enable, but don't run anything on their own."""
    routines_home.mkdir(parents=True, exist_ok=True)
    if any(d.is_dir() and not d.name.startswith(".") for d in routines_home.iterdir()):
        return 0                                    # not a fresh install — never clobber
    seed = repo_root() / "routine-seed"
    if not seed.is_dir():
        return 0
    n = 0
    for src in sorted(p for p in seed.iterdir() if p.is_dir()):
        dst = routines_home / src.name
        shutil.copytree(src, dst)
        if not (dst / ".git").is_dir():
            _git(dst, "init", "-q", "-b", "main")
        _git(dst, "config", "user.name", "routine-scheduler")
        _git(dst, "config", "user.email", "noreply@routine-scheduler.local")
        _git(dst, "add", "-A")
        _git(dst, "commit", "-qm", f"seed {src.name} routine")
        n += 1
    if n:
        log.warning("first boot: installed %d bundled meta routines (disabled)", n)
    return n


def seed_library(name: str, home: Path) -> None:
    """Populate an empty library from the built-in seed + git-init it (matches deploy/install.sh)."""
    root = repo_root()
    home.mkdir(parents=True, exist_ok=True)
    if name == "workflows":
        src = root / "library-seed" / "workflows"      # NOT the whole seed — fragments live in their own repo
        if src.is_dir():
            shutil.copytree(src, home / "workflows", dirs_exist_ok=True)
    elif name == "fragments":
        for f in sorted((root / "library-seed" / "fragments").glob("*.md")):
            shutil.copy(f, home / f.name)
    elif name == "utils":
        (home / "utils").mkdir(exist_ok=True)
        if (root / "util-seed" / "utils").is_dir():
            shutil.copytree(root / "util-seed" / "utils", home / "utils", dirs_exist_ok=True)
    else:
        raise ValueError(f"unknown library {name!r}")
    if not (home / ".git").is_dir():
        _git(home, "init", "-q", "-b", "main")
    _git(home, "config", "user.name", "routine-scheduler")
    _git(home, "config", "user.email", "noreply@routine-scheduler.local")
    _git(home, "add", "-A")
    _git(home, "commit", "-qm", f"seed {name} library")
    install_push_hook(home)
