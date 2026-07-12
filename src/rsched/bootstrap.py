"""First-boot bootstrap for a fresh (container) deploy. A host install runs deploy/install.sh; the
container has no install step, so the daemon + Settings do the equivalent: generate a config with a
random token if none exists (a fresh deploy must never serve an OPEN API), and seed a library from
the built-in defaults when the user chooses to create a new repo.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import yaml

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


# DEFAULT_FRAGMENTS entries introduced AFTER routines already existed never reach them via
# scaffold. Slugs listed here are added ONCE to every existing routine at daemon boot —
# tracked in a marker file, so a user who later deactivates one is never overridden.
ADOPT_FRAGMENTS = ["memory"]
_ADOPTED_MARKER = ".fragments-adopted.json"


def _ensure_library_fragment(fragments_home: Path, slug: str) -> str | None:
    """An existing library repo predates a new seed fragment (seed_libraries only runs at
    repo creation): copy the repo seed in — never overwriting — and commit, so the fragment
    exists as the grants/copy authority. Returns the library copy's content, or None."""
    dst = fragments_home / f"{slug}.md"
    if dst.exists():
        return dst.read_text(encoding="utf-8")
    src = repo_root() / "library-seed" / "fragments" / f"{slug}.md"
    if not fragments_home.is_dir() or not src.exists():
        return None
    shutil.copy(src, dst)
    _git(fragments_home.parent, "add", "-A")        # the library repo root (best-effort)
    _git(fragments_home.parent, "commit", "-qm", f"seed new default fragment: {slug}")
    return dst.read_text(encoding="utf-8")


def adopt_fragments(routines_home: Path, fragments_home: Path) -> int:
    """One-time propagation of new default fragments into EXISTING routines: append the slug
    to routine.yaml `fragments:` and drop the editable local copy. A slug is marked adopted
    only once the library copy exists (an unseeded library retries next boot). Returns the
    number of routine × fragment additions."""
    if not routines_home.is_dir():
        return 0
    marker = routines_home / _ADOPTED_MARKER
    try:
        done = set(json.loads(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    touched, newly_done = 0, set()
    for slug in ADOPT_FRAGMENTS:
        if slug in done:
            continue
        content = _ensure_library_fragment(fragments_home, slug)
        if content is None:
            continue
        for rdir in sorted(routines_home.iterdir()):
            if rdir.name.startswith(".") or not (rdir / "routine.yaml").is_file():
                continue                            # wizard sessions and strays stay untouched
            try:
                raw = yaml.safe_load((rdir / "routine.yaml").read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            frags = raw.get("fragments")
            local = rdir / "fragments" / f"{slug}.md"
            if frags is None:
                # No explicit list = the routine follows DEFAULT_FRAGMENTS (slug included);
                # only the editable local copy is missing.
                if local.exists():
                    continue
            elif slug in frags:
                continue
            else:
                raw["fragments"] = [*frags, slug]
                (rdir / "routine.yaml").write_text(
                    yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
            local.parent.mkdir(exist_ok=True)
            local.write_text(content, encoding="utf-8")
            _git(rdir, "add", "-A")
            _git(rdir, "commit", "-qm", f"adopt default fragment: {slug}")
            touched += 1
        newly_done.add(slug)
    if newly_done:
        marker.write_text(json.dumps(sorted(done | newly_done)) + "\n", encoding="utf-8")
    if touched:
        log.warning("adopted new default fragment(s) into %d routine(s)", touched)
    return touched


def seed_libraries(home: Path) -> None:
    """Populate an empty library repo (workflows/ + fragments/ + utils/) from the built-in seeds
    + git-init it (matches deploy/install.sh). The `gu` dispatcher is installed by
    utils_lib.ensure_library on first use."""
    root = repo_root()
    home.mkdir(parents=True, exist_ok=True)
    if (root / "library-seed" / "workflows").is_dir():
        shutil.copytree(root / "library-seed" / "workflows", home / "workflows", dirs_exist_ok=True)
    (home / "fragments").mkdir(exist_ok=True)
    for f in sorted((root / "library-seed" / "fragments").glob("*.md")):
        shutil.copy(f, home / "fragments" / f.name)
    (home / "utils").mkdir(exist_ok=True)
    if (root / "util-seed" / "utils").is_dir():
        shutil.copytree(root / "util-seed" / "utils", home / "utils", dirs_exist_ok=True)
    if not (home / ".git").is_dir():
        _git(home, "init", "-q", "-b", "main")
    _git(home, "config", "user.name", "routine-scheduler")
    _git(home, "config", "user.email", "noreply@routine-scheduler.local")
    _git(home, "add", "-A")
    _git(home, "commit", "-qm", "seed library repo")
    install_push_hook(home)
