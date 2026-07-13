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


def _install_seed_routine(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst)
    if not (dst / ".git").is_dir():
        _git(dst, "init", "-q", "-b", "main")
    _git(dst, "config", "user.name", "routine-scheduler")
    _git(dst, "config", "user.email", "noreply@routine-scheduler.local")
    _git(dst, "add", "-A")
    _git(dst, "commit", "-qm", f"seed {src.name} routine")


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
    exists as the grants authority. Returns the library copy's content, or None."""
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
    exists (an unseeded library retries next boot). Returns routine × permission additions."""
    if not routines_home.is_dir():
        return 0
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
            (rdir / "routine.yaml").write_text(
                yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
            _git(rdir, "add", "-A")
            _git(rdir, "commit", "-qm", f"adopt default permission: {slug}")
            touched += 1
        newly_done.add(slug)
    if newly_done:
        marker.write_text(json.dumps(sorted(done | newly_done)) + "\n", encoding="utf-8")
    if touched:
        log.warning("adopted new default permission(s) into %d routine(s)", touched)
    return touched


# The 2026-07 split: fragments became traits/ (practice prose, each routine's own copy) +
# permissions/ (engine-enforced grants in routine.yaml `permissions:`). These are the
# pre-split slugs that became permissions; everything else was prose and became a trait.
_LEGACY_PERMISSION_SLUGS = {"util-authoring", "util-authoring-autonomous",
                            "util-authoring-full-auto", "memory", "communication"}


def _detach_grants(raw: str, heading: str) -> str:
    """Rewrite a legacy fragment into a trait/permission doc: fix the heading keyword and
    (for traits) drop a grants: key that would now lint as an error."""
    import frontmatter as fm

    try:
        post = fm.loads(raw)
    except Exception:
        return raw.replace("# fragment:", f"# {heading}:", 1)
    if heading == "trait":
        post.metadata.pop("grants", None)
    post.content = post.content.replace("# fragment:", f"# {heading}:", 1)
    return fm.dumps(post, sort_keys=False) + "\n"


def migrate_fragments_split(routines_home: Path, library_home: Path) -> int:
    """One-time migration of a pre-split instance. Library: fragments/ is divided into
    traits/ + permissions/ — known seed slugs are replaced by the current repo seeds (the
    split rewrote them), unknown user fragments move mechanically by grants-presence.
    Routines: routine.yaml `fragments:` becomes `permissions:` (permission slugs kept,
    self-modification added — the behavior routines always had), local prose copies move to
    traits/, and main.md gains the Standing practices tail referencing them. Naturally
    idempotent: it triggers on the presence of the old layout. Returns touched routines."""
    root = repo_root()
    old = library_home / "fragments"
    if old.is_dir():
        for kind in ("traits", "permissions"):
            (library_home / kind).mkdir(exist_ok=True)
            seed_dir = root / "library-seed" / kind
            if seed_dir.is_dir():
                for f in sorted(seed_dir.glob("*.md")):
                    if not (library_home / kind / f.name).exists():
                        shutil.copy(f, library_home / kind / f.name)
        for f in sorted(old.glob("*.md")):
            if ((library_home / "traits" / f.name).exists()
                    or (library_home / "permissions" / f.name).exists()):
                continue                             # replaced by a current seed
            raw = f.read_text(encoding="utf-8")
            try:
                import frontmatter as fm
                has_grants = bool((fm.loads(raw).metadata or {}).get("grants"))
            except Exception:
                has_grants = False
            kind = ("permissions" if f.stem in _LEGACY_PERMISSION_SLUGS or has_grants
                    else "traits")
            (library_home / kind / f.name).write_text(
                _detach_grants(raw, kind[:-1]), encoding="utf-8")
        shutil.rmtree(old)
        _git(library_home, "add", "-A")
        _git(library_home, "commit", "-qm", "migrate: fragments split into traits + permissions")
        log.warning("library migrated: fragments/ split into traits/ + permissions/")

    if not routines_home.is_dir():
        return 0
    from .config import DEFAULT_PERMISSIONS
    from .library_docs import DOC_RE
    from .workflows.scaffold import _with_practices_tail

    touched = 0
    for rdir in sorted(routines_home.iterdir()):
        if rdir.name.startswith(".") or not (rdir / "routine.yaml").is_file():
            continue
        try:
            raw = yaml.safe_load((rdir / "routine.yaml").read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        frag_dir = rdir / "fragments"
        if "fragments" not in raw and not frag_dir.is_dir():
            continue
        fragments = raw.pop("fragments", None)
        if fragments is None:
            perms = list(DEFAULT_PERMISSIONS)
        else:
            perms = [f for f in fragments if f in _LEGACY_PERMISSION_SLUGS]
            perms.append("self-modification")
        raw["permissions"] = list(dict.fromkeys(perms))
        (rdir / "routine.yaml").write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        trait_summaries: dict[str, str] = {}
        if frag_dir.is_dir():
            (rdir / "traits").mkdir(exist_ok=True)
            for f in sorted(frag_dir.glob("*.md")):
                if f.stem not in _LEGACY_PERMISSION_SLUGS:
                    body = _detach_grants(f.read_text(encoding="utf-8"), "trait")
                    (rdir / "traits" / f.name).write_text(body, encoding="utf-8")
                    m = DOC_RE.search(body)
                    trait_summaries[f.stem] = m.group("summary").strip() if m else ""
            shutil.rmtree(frag_dir)
        main = rdir / "main.md"
        if main.is_file() and trait_summaries:
            text = main.read_text(encoding="utf-8")
            new_text = _with_practices_tail(text.rstrip() + "\n", trait_summaries)
            if new_text != text:
                main.write_text(new_text, encoding="utf-8")
        _git(rdir, "add", "-A")
        _git(rdir, "commit", "-qm", "migrate: fragments split into traits + permissions")
        touched += 1
    if touched:
        log.warning("migrated %d routine(s) to the traits + permissions split", touched)
    return touched


# The 2026-07 improvement consolidation: the five improve-* traits are retired from
# per-routine adaptation — the routine-improver meta routine now runs those lenses across
# every routine that doesn't set `exclude_from_improvement` — and library-sync stopped
# being a routine (it's a scheduled daemon job now, see library_sync.py).
_IMPROVE_TRAITS = ("improve-bugfix", "improve-research", "improve-features",
                   "improve-ui", "improve-efficiency")


_IMPROVE_ENTRY_RE = re.compile(
    r"""["'](?:improve-(?:bugfix|research|features|ui|efficiency))["']\s*,?\s*""")
_INCLUDES_LIST_RE = re.compile(r'(["\']includes["\']\s*:\s*\[)(.*?)(\])', re.DOTALL)


def _strip_improve_includes(library_home: Path) -> int:
    """Remove retired improve-* entries from library workflows' META `includes` lists —
    scoped to the list literal, so prose mentioning a lens elsewhere is untouched. (The
    workflow files are data, parsed with ast, never executed.) Returns files changed."""
    wf_dir = library_home / "workflows"
    if not wf_dir.is_dir():
        return 0
    changed = 0
    for f in sorted(wf_dir.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        new = _INCLUDES_LIST_RE.sub(
            lambda m: m.group(1) + _IMPROVE_ENTRY_RE.sub("", m.group(2)) + m.group(3), text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
    return changed


# The exact acting-authorization paragraph the self-audit seed carried before the split —
# it gated acting on ACTIVE improve-* fragment files, which this migration deletes. An
# installed copy still carrying it would read itself as report-only forever.
_SELF_AUDIT_LEGACY = """- **Changing anything is governed by my fragment toggles.** Your ACTIVE `improve-*` fragments
  (listed in the state digest) are the only authorization to act on findings, each in its own
  lens: `improve-bugfix` → defect fixes and the logging/telemetry a thin suspicion needs;
  `improve-efficiency` → waste reduction; `improve-features` → small self-contained affordances;
  `improve-ui` → interface/artifact quality; `improve-research` grounds the others in current
  best practice. **With none of them active, this is a report-only audit: change nothing.**
  All acting is **test-gated** — commit + push, log to the changelog, request the restart.
- Act on my feedback from the Audit tab: comments on findings, decisions I settle, general
  notes. A decision I settled is explicit authorization — apply it (test-gated) regardless of
  which fragments are active."""
_SELF_AUDIT_CURRENT = """- **Act on findings, in these lenses**: defect fixes (plus the logging/telemetry a thin
  suspicion needs); waste reduction; small self-contained affordances; interface/artifact
  quality — each grounded in current best practice (research before you patch). All acting is
  **test-gated** — commit + push, log to the changelog, request the restart. Changing the
  action-schema / transcript-event / ownership contracts, or anything scope-shaped, is a
  decision for me, not a fix.
- Act on my feedback from the Audit tab: comments on findings, decisions I settle, general
  notes. A decision I settled is explicit authorization — apply it (test-gated).
- Your remit is the scheduler CODE and daemon behaviour. Improving individual routines'
  recipes is the routine-improver meta routine's job, not yours — findings about a specific
  routine become report entries or decisions naming it."""


# Same idea for the seed step modules that referenced the fragment gate: exact legacy
# text → current text; anything user-edited stays and is warned about.
_SELF_AUDIT_STEP_FIXES = [
    ("gather-evidence.md",
     "## D. UI friction — ONLY if `improve-ui` is among your active fragments (see the state digest)",
     "## D. UI friction"),
    ("gather-evidence.md",
     """  These feed the improve-ui lens in analyse-findings; skip this section entirely when the
  fragment is inactive.""",
     "  These feed the interface-quality lens in analyse-findings."),
    ("separate-decisions.md",
     """   - **Lens condition**: an ACTIVE `improve-*` fragment (state digest) covers it —
     bugfix → defects + instrumentation; efficiency → waste; features → small self-contained
     affordances; ui → interface/artifact quality. **No active lens covers it → it cannot go
     to APPLY**, however self-evident; make it a decision or a report line instead.
     (Exception: a decision the user settled is explicit authorization on its own.)""",
     """   - **Lens condition**: it falls in one of your lenses — defects + instrumentation; waste;
     small self-contained affordances; interface/artifact quality. Outside every lens → it
     cannot go to APPLY, however self-evident; make it a decision or a report line instead.
     (A decision the user settled is explicit authorization on its own.)"""),
    ("analyse-findings.md",
     """That instrumentation is a real code change: it reaches `act-apply-fixes`
   only under an active `improve-bugfix` lens; otherwise file it as a decision.""",
     """That instrumentation is a real code change and goes through
   `act-apply-fixes` like any other (test-gated)."""),
    ("act-apply-fixes.md",
     """APPLY may only contain what the autonomy gate authorized: items covered by an ACTIVE
`improve-*` fragment, plus decisions the user settled. **If no `improve-*` fragment is active
and no settled decision is pending, APPLY must be empty** — this is a report-only run.
If APPLY is empty, skip straight to Next (a no-change run is a good run — say so in the report).""",
     """APPLY may only contain what the autonomy gate authorized: items inside your lenses that pass
the safety condition, plus decisions the user settled.
If APPLY is empty, skip straight to Next (a no-change run is a good run — say so in the report)."""),
]


def _repoint_self_audit(rdir: Path) -> None:
    """Best-effort: swap the legacy fragment-gated acting prose for the current lens-scoped
    version, in instruction.md and the seed step modules. Only exact matches are replaced
    (user-edited text is theirs); leftover fragment references are warned about instead."""
    ins = rdir / "instruction.md"
    if not ins.is_file():
        return
    changed = False
    text = ins.read_text(encoding="utf-8")
    if _SELF_AUDIT_LEGACY in text:
        ins.write_text(text.replace(_SELF_AUDIT_LEGACY, _SELF_AUDIT_CURRENT), encoding="utf-8")
        changed = True
    for name, old, new in _SELF_AUDIT_STEP_FIXES:
        step = rdir / "steps" / name
        if step.is_file() and old in (t := step.read_text(encoding="utf-8")):
            step.write_text(t.replace(old, new), encoding="utf-8")
            changed = True
    if changed:
        _git(rdir, "add", "-A")
        _git(rdir, "commit", "-qm", "migrate: acting is lens-scoped, no fragment toggles")
    leftovers = [p.name for p in [ins, *sorted((rdir / "steps").glob("*.md"))]
                 if p.is_file() and "fragment" in p.read_text(encoding="utf-8")]
    if leftovers:
        log.warning("self-audit still references fragment toggles in %s — the improve-* "
                    "files are gone; review manually", ", ".join(leftovers))


def migrate_improvement_split(routines_home: Path, library_home: Path) -> int:
    """Carry an existing instance over to the consolidated-improvement layout. Every step
    triggers only on the old layout, so the whole thing is naturally idempotent:
    1. retire an installed library-sync routine into .archive/ (its job moved to the daemon),
    2. rename an installed meta-workflows routine to workflow-curator,
    3. install the routine-improver seed (seed_routines only runs on fresh installs),
    4. delete the improve-* traits from the live library (the wizard stops offering them),
    5. repoint self-audit's acting prose off the deleted fragment gate,
    6. strip improve-* trait copies + their main.md references from every routine.
    Returns how many routines step 6 touched."""
    if not routines_home.is_dir():
        return 0

    old_sync = routines_home / "library-sync"
    if old_sync.is_dir():
        try:
            raw = yaml.safe_load((old_sync / "routine.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            raw = {}
        if (raw.get("workflow") or {}).get("library_slug") == "library-sync":
            archive = routines_home / ".archive"
            archive.mkdir(exist_ok=True)
            dest = archive / "library-sync-retired"
            if not dest.exists():
                old_sync.rename(dest)
                log.warning("library-sync routine retired to %s — it is a scheduled daemon "
                            "job now (Settings → Library sync)", dest)

    old_meta = routines_home / "meta-workflows"
    new_meta = routines_home / "workflow-curator"
    if old_meta.is_dir() and not new_meta.exists():
        old_meta.rename(new_meta)
        try:
            raw = yaml.safe_load((new_meta / "routine.yaml").read_text(encoding="utf-8")) or {}
            raw["slug"] = "workflow-curator"
            if raw.get("name") in ("Meta: workflow library", "meta-workflows"):
                raw["name"] = "Workflow curator"
            (new_meta / "routine.yaml").write_text(
                yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
            main = new_meta / "main.md"
            if main.is_file():
                main.write_text(main.read_text(encoding="utf-8").replace(
                    "slug: meta-workflows\n", "slug: workflow-curator\n", 1), encoding="utf-8")
        except (OSError, yaml.YAMLError):
            pass
        _git(new_meta, "add", "-A")
        _git(new_meta, "commit", "-qm", "migrate: renamed to workflow-curator")
        log.warning("meta-workflows routine renamed to workflow-curator")

    improver_seed = repo_root() / "routine-seed" / "routine-improver"
    improver = routines_home / "routine-improver"
    if improver_seed.is_dir() and not improver.exists():
        _install_seed_routine(improver_seed, improver)
        log.warning("installed the routine-improver meta routine (disabled) — it now owns "
                    "the improvement passes")

    removed = []
    for slug in _IMPROVE_TRAITS:
        f = library_home / "traits" / f"{slug}.md"
        if f.exists():
            f.unlink()
            removed.append(slug)
    # library workflows referencing the deleted traits would lint red forever (seed-sync
    # never overwrites local copies) — strip the stale includes entries in place
    stripped = _strip_improve_includes(library_home)
    if removed or stripped:
        _git(library_home, "add", "-A")
        _git(library_home, "commit", "-qm",
             "migrate: improve-* traits retired (routine-improver owns the lenses)")

    _repoint_self_audit(routines_home / "self-audit")

    touched = 0
    for rdir in sorted(routines_home.iterdir()):
        if rdir.name.startswith(".") or not (rdir / "routine.yaml").is_file():
            continue
        if rdir.name == "routine-improver":
            continue   # its lens modules are its recipe, not per-routine trait copies
        changed = False
        for slug in _IMPROVE_TRAITS:
            f = rdir / "traits" / f"{slug}.md"
            if f.exists():
                f.unlink()
                changed = True
        main = rdir / "main.md"
        if main.is_file():
            lines = main.read_text(encoding="utf-8").splitlines(keepends=True)
            kept = [ln for ln in lines
                    if "traits/improve-" not in ln
                    and ln.strip() not in {f"- {s}" for s in _IMPROVE_TRAITS}]
            if len(kept) != len(lines):
                main.write_text("".join(kept), encoding="utf-8")
                changed = True
        if changed:
            _git(rdir, "add", "-A")
            _git(rdir, "commit", "-qm",
                 "migrate: improvement passes moved to the routine-improver meta routine")
            touched += 1
    if touched:
        log.warning("stripped improve-* trait copies from %d routine(s)", touched)
    return touched


# The 2026-07 centralization: self-modification stopped being a default permission —
# recipe improvement lives in the routine-improver meta routine, the only default holder.
_SELF_MOD_MARKER = ".self-modification-revoked"


def revoke_self_modification(routines_home: Path, conversations_home: Path) -> int:
    """One-time revoke of `self-modification` from every existing routine and conversation
    EXCEPT routine-improver (it improves itself under the same gate everyone else lost).
    Marker-tracked: a grant the user makes afterwards is theirs to keep."""
    if not routines_home.is_dir():
        return 0
    marker = routines_home / _SELF_MOD_MARKER
    if marker.exists():
        return 0
    touched = 0
    for home in (routines_home, conversations_home):
        if not home or not Path(home).is_dir():
            continue
        for rdir in sorted(Path(home).iterdir()):
            cfg = rdir / "routine.yaml"
            if rdir.name.startswith(".") or rdir.name == "routine-improver" or not cfg.is_file():
                continue
            try:
                raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            perms = raw.get("permissions")
            if not isinstance(perms, list) or "self-modification" not in perms:
                continue
            raw["permissions"] = [p for p in perms if p != "self-modification"]
            cfg.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                           encoding="utf-8")
            _git(rdir, "add", "-A")
            _git(rdir, "commit", "-qm",
                 "revoke self-modification (recipe improvement is the routine-improver's job)")
            touched += 1
    marker.write_text("done\n", encoding="utf-8")
    if touched:
        log.warning("self-modification revoked from %d routine(s)/conversation(s) — "
                    "the routine-improver is the default holder now", touched)
    return touched


# The 2026-07 budget change: max_total_tokens -1 = unlimited became the default, and every
# EXISTING routine and conversation switches too (Mark's call). One-time: a finite cap the
# user sets afterwards is never overridden again.
_TOKENS_MARKER = ".unlimited-tokens-adopted"


def adopt_unlimited_tokens(routines_home: Path, conversations_home: Path) -> int:
    """Set budgets.max_total_tokens to -1 (unlimited) in every existing routine and
    conversation that pins a different value. Dirs without an explicit value already
    follow DEFAULT_BUDGETS. Marker-tracked so it runs exactly once."""
    if not routines_home.is_dir():
        return 0
    marker = routines_home / _TOKENS_MARKER
    if marker.exists():
        return 0
    touched = 0
    for home in (routines_home, conversations_home):
        if not home or not Path(home).is_dir():
            continue
        for rdir in sorted(Path(home).iterdir()):
            cfg = rdir / "routine.yaml"
            if rdir.name.startswith(".") or not cfg.is_file():
                continue
            try:
                raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            budgets = raw.get("budgets")
            if not isinstance(budgets, dict) or budgets.get("max_total_tokens") in (None, -1):
                continue
            budgets["max_total_tokens"] = -1
            cfg.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                           encoding="utf-8")
            _git(rdir, "add", "-A")          # no-op for conversations (never git-versioned)
            _git(rdir, "commit", "-qm", "adopt: unlimited token budget (-1)")
            touched += 1
    marker.write_text("done\n", encoding="utf-8")
    if touched:
        log.warning("token budget set to unlimited (-1) in %d routine(s)/conversation(s)", touched)
    return touched


def seed_libraries(home: Path) -> None:
    """Populate an empty library repo (workflows/ + traits/ + permissions/ + utils/) from the
    built-in seeds + git-init it (matches deploy/install.sh). The `gu` dispatcher is installed
    by utils_lib.ensure_library on first use."""
    root = repo_root()
    home.mkdir(parents=True, exist_ok=True)
    if (root / "library-seed" / "workflows").is_dir():
        shutil.copytree(root / "library-seed" / "workflows", home / "workflows", dirs_exist_ok=True)
    for kind in ("traits", "permissions"):
        (home / kind).mkdir(exist_ok=True)
        if (root / "library-seed" / kind).is_dir():
            for f in sorted((root / "library-seed" / kind).glob("*.md")):
                shutil.copy(f, home / kind / f.name)
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
    absent file verbatim; NEVER overwrites (local edits win). Returns how many landed."""
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
    modifications stay untouched). Returns how many were installed."""
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
