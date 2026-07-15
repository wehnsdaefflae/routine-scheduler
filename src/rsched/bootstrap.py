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

from .paths import atomic_write, config_file

log = logging.getLogger("rsched.bootstrap")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_config() -> bool:
    """Create config.yaml with a random token if it's missing. Returns True if it generated one.
    Without this a fresh deploy has an empty token → auth is disabled → an open API on the LAN.
    """
    path = config_file()
    if path.exists():
        return False
    token = secrets.token_urlsafe(24)
    example = repo_root() / "config" / "config.example.yaml"
    if example.exists():
        text = re.sub(r'token:\s*"change-me".*', f'token: "{token}"',
                      example.read_text(encoding="utf-8"))
    else:
        text = f'bind: 127.0.0.1\nport: 8321\ntoken: "{token}"\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.warning("first boot: generated %s with a fresh access token", path)
    return True


def _git(home: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(home), *args], capture_output=True, check=False)


def install_push_hook(home: Path) -> None:
    """Best-effort auto-push-on-commit hook, so generated library changes sync to the remote."""
    src = repo_root() / "deploy" / "post-commit"
    if src.exists() and (home / ".git").is_dir():
        dst = home / ".git" / "hooks" / "post-commit"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        dst.chmod(0o755)


def _install_seed_routine(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst)
    if not (dst / ".git").is_dir():
        _git(dst, "init", "-q", "-b", "main")
    _git(dst, "config", "user.name", "routine-scheduler")
    _git(dst, "config", "user.email", "noreply@routine-scheduler.local")
    _git(dst, "add", "-A")
    _git(dst, "commit", "-qm", f"seed {src.name} routine")


def adopt_seed_routine(routines_home: Path, slug: str) -> bool:
    """Install ONE bundled meta routine into an EXISTING instance — how a seed added
    after first boot reaches deployments (seed_routines runs only on fresh installs).
    Idempotent, and an archived copy is respected: the user removed it on purpose.
    """
    seed = repo_root() / "routine-seed" / slug
    dst = routines_home / slug
    if not seed.is_dir() or not routines_home.is_dir() or dst.exists():
        return False
    archive = routines_home / ".archive"
    if archive.is_dir() and any(d.name == slug or d.name.startswith(f"{slug}-")
                                for d in archive.iterdir()):
        return False
    _install_seed_routine(seed, dst)
    log.warning("installed the %s meta routine (disabled) — enable it on its routine page", slug)
    return True


def seed_routines(routines_home: Path) -> int:
    """On a fresh install (no routines yet), install the bundled meta routines — disabled, so they
    show up under the 'meta' tag for the user to enable, but don't run anything on their own.
    """
    routines_home.mkdir(parents=True, exist_ok=True)
    if any(d.is_dir() and not d.name.startswith(".") for d in routines_home.iterdir()):
        return 0                                    # not a fresh install — never clobber
    seed = repo_root() / "routine-seed"
    if not seed.is_dir():
        return 0
    n = 0
    for src in sorted(p for p in seed.iterdir() if p.is_dir()):
        _install_seed_routine(src, routines_home / src.name)
        n += 1
    if n:
        log.warning("first boot: installed %d bundled meta routines (disabled)", n)
    return n


# DEFAULT_PERMISSIONS entries introduced AFTER routines already existed never reach them via
# scaffold. Slugs listed here are added ONCE to every existing routine at daemon boot —
# tracked in a marker file, so a user who later revokes one is never overridden.
ADOPT_PERMISSIONS: list[str] = []
_ADOPTED_MARKER = ".permissions-adopted.json"


def _ensure_library_permission(permissions_home: Path, slug: str) -> str | None:
    """An existing library repo predates a new seed permission (seed_libraries only runs at
    repo creation): copy the repo seed in — never overwriting — and commit, so the permission
    exists as the grants authority. Returns the library copy's content, or None.
    """
    dst = permissions_home / f"{slug}.md"
    if dst.exists():
        return dst.read_text(encoding="utf-8")
    src = repo_root() / "library-seed" / "permissions" / f"{slug}.md"
    if not permissions_home.is_dir() or not src.exists():
        return None
    shutil.copy(src, dst)
    _git(permissions_home.parent, "add", "-A")        # the library repo root (best-effort)
    _git(permissions_home.parent, "commit", "-qm", f"seed new default permission: {slug}")
    return dst.read_text(encoding="utf-8")


def adopt_permissions(routines_home: Path, permissions_home: Path) -> int:
    """One-time propagation of new default permissions into EXISTING routines: append the
    slug to routine.yaml `permissions:`. A slug is marked adopted only once the library copy
    exists (an unseeded library retries next boot). Returns routine × permission additions.
    """
    if not ADOPT_PERMISSIONS or not routines_home.is_dir():
        return 0   # nothing pending adoption — skip the marker read and routine walk entirely
    marker = routines_home / _ADOPTED_MARKER
    try:
        done = set(json.loads(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    touched, newly_done = 0, set()
    for slug in ADOPT_PERMISSIONS:
        if slug in done:
            continue
        if _ensure_library_permission(permissions_home, slug) is None:
            continue
        for rdir in sorted(routines_home.iterdir()):
            if rdir.name.startswith(".") or not (rdir / "routine.yaml").is_file():
                continue                            # wizard sessions and strays stay untouched
            try:
                raw = yaml.safe_load((rdir / "routine.yaml").read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            perms = raw.get("permissions")
            if perms is None or slug in perms:
                # no explicit list = the routine follows DEFAULT_PERMISSIONS (slug included)
                continue
            raw["permissions"] = [*perms, slug]
            # the activation cascade: switching the doc on switches on what it requires
            if isinstance(raw.get("capabilities"), dict):
                from .grants import read_library_requires

                _merge_caps(raw["capabilities"],
                            read_library_requires(permissions_home).get(slug) or {})
            atomic_write(rdir / "routine.yaml",
                         yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
            _git(rdir, "add", "-A")
            _git(rdir, "commit", "-qm", f"adopt default permission: {slug}")
            touched += 1
        newly_done.add(slug)
    if newly_done:
        marker.write_text(json.dumps(sorted(done | newly_done)) + "\n", encoding="utf-8")
    if touched:
        log.warning("adopted new default permission(s) into %d routine(s)", touched)
    return touched


# Capability-mapping merge for adopt_permissions' activation cascade (most permissive
# confirm/runs wins). Historical data migrations are deliberately NOT kept in this
# module: each ran once on the production instance and was deleted after convergence —
# to convert a pre-0.8 backup, boot it on the matching older tag first.
_CONFIRM_RANK = {"always": 0, "creations": 1, "never": 2}
_RUNS_RANK = {"none": 0, "last": 1, "all": 2}


def _merge_caps(caps: dict, extra: dict) -> None:
    """Union `extra` into `caps` in place — additive: most permissive confirm/runs wins."""
    for key in ("actions", "utils"):
        caps.setdefault(key, [])
        caps[key] += [v for v in extra.get(key) or [] if v not in caps[key]]
    if _RUNS_RANK.get(extra.get("runs") or "none", 0) \
            > _RUNS_RANK.get(caps.get("runs") or "none", 0):
        caps["runs"] = extra["runs"]
    if _CONFIRM_RANK.get(extra.get("confirm") or "always", 0) \
            > _CONFIRM_RANK.get(caps.get("confirm") or "always", 0):
        caps["confirm"] = extra["confirm"]


def seed_libraries(home: Path) -> None:
    """Populate an empty library repo (workflows/ + traits/ + permissions/ + utils/) from the
    built-in seeds + git-init it (matches deploy/install.sh). The `gu` dispatcher is installed
    by utils_lib.ensure_library on first use.
    """
    root = repo_root()
    home.mkdir(parents=True, exist_ok=True)
    if (root / "library-seed" / "workflows").is_dir():
        shutil.copytree(root / "library-seed" / "workflows", home / "workflows", dirs_exist_ok=True)
    for kind in ("traits", "permissions"):
        (home / kind).mkdir(exist_ok=True)
        if (root / "library-seed" / kind).is_dir():
            for f in sorted((root / "library-seed" / kind).glob("*.md")):
                shutil.copy(f, home / kind / f.name)
    # playbooks are subfolders (MAIN.md + detail files), so copy the whole tree
    if (root / "library-seed" / "playbooks").is_dir():
        shutil.copytree(root / "library-seed" / "playbooks", home / "playbooks", dirs_exist_ok=True)
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


def sync_seed_library_docs(libraries_home: Path) -> int:
    """Install seed workflows/traits/permissions MISSING from the live library (runs at
    every daemon boot, like sync_seed_utils). seed_libraries only runs at repo creation,
    so a pattern or trait added to library-seed/ later — e.g. the `converse` workflow the
    Conversations tab materializes — would never reach an existing instance. Copies each
    absent file verbatim; NEVER overwrites (local edits win). Returns how many landed.
    """
    root = repo_root() / "library-seed"
    installed: list[str] = []
    for kind, pattern in (("workflows", "*.py"), ("traits", "*.md"), ("permissions", "*.md")):
        src = root / kind
        dest = libraries_home / kind
        if not src.is_dir() or not libraries_home.is_dir():
            continue
        dest.mkdir(exist_ok=True)
        for f in sorted(src.glob(pattern)):
            if not (dest / f.name).exists():
                shutil.copy(f, dest / f.name)
                installed.append(f"{kind}/{f.name}")
    # playbooks are subfolders (MAIN.md + detail files), not flat files — copy whole
    # subfolders missing from the live library (mirrors sync_seed_utils).
    pb_src, pb_dest = root / "playbooks", libraries_home / "playbooks"
    if pb_src.is_dir() and libraries_home.is_dir():
        pb_dest.mkdir(exist_ok=True)
        for d in sorted(p for p in pb_src.iterdir() if p.is_dir()):
            if not (pb_dest / d.name).exists():
                shutil.copytree(d, pb_dest / d.name)
                installed.append(f"playbooks/{d.name}")
    if installed:
        log.warning("seed-sync: installed new library doc(s): %s", ", ".join(installed))
        _git(libraries_home, "add", "-A")
        _git(libraries_home, "commit", "-qm",
             f"seed-sync: install new library doc(s): {', '.join(installed)}")
    return len(installed)


def sync_seed_utils(libraries_home: Path) -> int:
    """Install seed utils MISSING from the live util library (runs at every daemon boot).
    Bootstrap seeds utils only once, so a util added to util-seed/ after an instance was
    created never reached it — a permission could point at a util that doesn't exist
    (the reserved 'shell' util did exactly that). Copies each absent
    util-seed/utils/<name> verbatim; NEVER touches an existing util dir (local
    modifications stay untouched). Returns how many were installed.
    """
    src = repo_root() / "util-seed" / "utils"
    dest = libraries_home / "utils"
    if not src.is_dir() or not dest.is_dir():
        return 0   # fresh deploys get everything via seed_libraries instead
    installed = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        target = dest / d.name
        if target.exists():
            continue
        shutil.copytree(d, target)
        installed.append(d.name)
    if installed:
        log.warning("seed-sync: installed new seed util(s): %s", ", ".join(installed))
        _git(libraries_home, "add", "-A")
        _git(libraries_home, "commit", "-qm",
             f"seed-sync: install new seed util(s): {', '.join(installed)}")
    return len(installed)
