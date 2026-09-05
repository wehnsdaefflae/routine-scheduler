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

from . import libgit
from .paths import atomic_write, atomic_write_yaml, config_file, read_yaml, repo_root

log = logging.getLogger("rsched.bootstrap")


def ensure_config() -> bool:
    """Create config.yaml with random tokens if it's missing, and add a `routine_token`
    to an existing config that predates the two-tier auth (R94) — the primary must never
    double as the routine tier, or the seal is vacuous. Returns True if it generated the
    whole config. Without this a fresh deploy has an empty token → auth is disabled → an
    open API on the LAN.
    """
    path = config_file()
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if not re.search(r"(?m)^routine_token:", text):
            atomic_write(path, text.rstrip()
                         + f'\nroutine_token: "{secrets.token_urlsafe(24)}"\n')
            log.warning("boot: added a routine_token to %s (R94 two-tier auth)", path)
        return False
    token = secrets.token_urlsafe(24)
    routine_token = secrets.token_urlsafe(24)
    example = repo_root() / "config" / "config.example.yaml"
    if example.exists():
        # replace WHATEVER token line the example carries (a drifted placeholder must
        # never ship as a known token / an empty token = an open API); none → append
        text, n = re.subn(r"(?m)^token:.*$", f'token: "{token}"',
                          example.read_text(encoding="utf-8"), count=1)
        if not n:
            text = text.rstrip() + f'\ntoken: "{token}"\n'
        text, n = re.subn(r"(?m)^routine_token:.*$", f'routine_token: "{routine_token}"',
                          text, count=1)
        if not n:
            text = text.rstrip() + f'\nroutine_token: "{routine_token}"\n'
    else:
        text = (f'bind: 127.0.0.1\nport: 8321\ntoken: "{token}"\n'
                f'routine_token: "{routine_token}"\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
    log.warning("first boot: generated %s with fresh access tokens", path)
    return True


# DEFAULT_PERMISSIONS entries introduced AFTER routines already existed never reach them via
# scaffold. Slugs listed here are added ONCE to every existing routine at daemon boot —
# tracked in a marker file, so a user who later revokes one is never overridden.
ADOPT_PERMISSIONS: list[str] = ["global-utils", "reminders"]
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
    libgit.commit(permissions_home.parent, f"seed new default permission: {slug}",
                  paths=[f"{permissions_home.name}/{slug}.md"])
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
                continue                            # clarify workspaces and strays stay untouched
            try:
                raw = read_yaml(rdir / "routine.yaml", {})
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
            atomic_write_yaml(rdir / "routine.yaml", raw)
            libgit.commit(rdir, f"adopt default permission: {slug}")
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
    """Populate an empty library repo (workflows/ + rules/ + permissions/ + templates/ +
    reminders/ + utils/) from the built-in seeds + git-init it (matches deploy/install.sh).
    The `gu` dispatcher is installed by utils_lib.ensure_library on first use.
    """
    root = repo_root()
    home.mkdir(parents=True, exist_ok=True)
    if (root / "library-seed" / "workflows").is_dir():
        shutil.copytree(root / "library-seed" / "workflows", home / "workflows", dirs_exist_ok=True)
    for kind in ("rules", "permissions", "templates", "reminders"):
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
        libgit.init_repo(home, first_commit="seed library repo")
    else:
        libgit.commit(home, "seed library repo")
        libgit.install_push_hook(home)


#: The four flat library doc kinds the boot sync tops up, with the glob that finds them.
#: `templates` is here because a settings TEMPLATE is read LIVE at creation and by the routine
#: page's adopt action (templates.config_for) — a template that only ever lands when the repo is
#: first created means a template added to the seed later reaches no existing instance at all.
#: `reminders` carries only its README — the curated cautions themselves are written by
#: runs, under approval — but the DIRECTORY has to exist and be tracked, or the store is
#: invisible in the repo until the first write and the Library tab has nothing to list.
SEED_DOC_KINDS = (("workflows", "*.py"), ("rules", "*.md"), ("permissions", "*.md"),
                  ("templates", "*.md"), ("reminders", "*.md"))


def sync_seed_library_docs(libraries_home: Path) -> int:
    """Install seed workflows/rules/permissions/templates MISSING from the live library (runs at
    every daemon boot, like sync_seed_utils). seed_libraries only runs at repo creation, so a
    pattern or rule added to library-seed/ later — e.g. the `converse` workflow the
    Conversations tab materializes — would never reach an existing instance. Copies each
    absent file verbatim; NEVER overwrites (local edits win). Returns how many landed.

    ADD-ONLY is not enough on its own: a doc the operator DELETED in the Library tab is also
    "missing", so without a guard every restart resurrected it — and pushed it, since the library
    repo has a post-commit push hook. `libgit.path_was_deleted` makes a deletion stick, exactly
    as `sync_seed_utils` has always done for utils. (`converse` is not at risk: it is refused at
    delete time by name, api_workflows.delete_workflow, rather than restored after the fact.)

    Content DRIFT is deliberately still not synced. These files are user-editable, so an
    overwrite would silently discard an operator's edit; a seed doc whose text must change on a
    live instance is converted by a one-shot migration instead (the CLAUDE.md rule).
    """
    root = repo_root() / "library-seed"
    installed: list[str] = []
    for kind, pattern in SEED_DOC_KINDS:
        src = root / kind
        dest = libraries_home / kind
        if not src.is_dir() or not libraries_home.is_dir():
            continue
        dest.mkdir(exist_ok=True)
        for f in sorted(src.glob(pattern)):
            rel = f"{kind}/{f.name}"
            if not (dest / f.name).exists() and not libgit.path_was_deleted(libraries_home, rel):
                shutil.copy(f, dest / f.name)
                installed.append(rel)
    # playbooks are subfolders (MAIN.md + detail files), not flat files — copy whole
    # subfolders missing from the live library (mirrors sync_seed_utils).
    pb_src, pb_dest = root / "playbooks", libraries_home / "playbooks"
    if pb_src.is_dir() and libraries_home.is_dir():
        pb_dest.mkdir(exist_ok=True)
        for d in sorted(p for p in pb_src.iterdir() if p.is_dir()):
            if not (pb_dest / d.name).exists() \
                    and not libgit.path_was_deleted(libraries_home, f"playbooks/{d.name}/MAIN.md"):
                shutil.copytree(d, pb_dest / d.name)
                installed.append(f"playbooks/{d.name}")
    if installed:
        log.warning("seed-sync: installed new library doc(s): %s", ", ".join(installed))
        libgit.commit(libraries_home,
                      f"seed-sync: install new library doc(s): {', '.join(installed)}",
                      paths=installed)
    return len(installed)


def adopt_library_edits(libraries_home: Path) -> bool:
    """Commit whatever OUT-OF-BAND edits the live library repo is carrying (runs at every
    daemon boot, after the seed syncs). Every managed write path commits what it writes
    (write_util / write_rule, the web save endpoints, seed-sync) — but a conversation
    editing library files through a filesystem grant, or the user in an editor, writes
    directly and nothing ever commits it: on 2026-08-13 the repo accumulated six loose
    rule/permission files across one working day (R332/R335), invisible to history — the
    one thing the repo exists to keep. Adopting the edits verbatim beats leaving them
    loose; the linter still reports nonconforming content on its own channel.
    """
    if not (libraries_home / ".git").is_dir():
        return False
    try:
        r = libgit.git(libraries_home, "status", "--porcelain")
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0 or not r.stdout.strip():
        return False
    log.warning("boot: adopting out-of-band library edit(s):\n%s", r.stdout.strip())
    return libgit.commit(libraries_home, "boot: adopt out-of-band library edits")


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
    from . import utils_lib

    installed = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        target = dest / d.name
        if target.exists():
            continue
        # Never resurrect a util the USER deleted (the library's git history knows) — the
        # same rule the engine enforces on write_util (interact.recreate_denial). A missing
        # seed util with no deletion history is genuinely new and lands normally.
        if utils_lib.was_deleted(libraries_home, d.name):
            continue
        shutil.copytree(d, target)
        installed.append(d.name)
    if installed:
        log.warning("seed-sync: installed new seed util(s): %s", ", ".join(installed))
        libgit.commit(libraries_home,
                      f"seed-sync: install new seed util(s): {', '.join(installed)}",
                      paths=[f"utils/{n}" for n in installed])
    return len(installed)
