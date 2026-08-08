"""MIGRATION(expires=2026-09-30): traits → general rules (0.164.0).

Converts the pre-0.164 shape, where every routine carried its own ADAPTED copies of the
practice modules under `<dir>/traits/`, to the current one: a single library copy under
`<library>/rules/`, held by SLUG in each routine.yaml's `rules:` list.

Runs once at daemon boot on the production instance, then gets deleted (the
delete-after-convergence policy — CLAUDE.md). Every step is idempotent, so a partial run
followed by a restart converges.

Per instance:
  1. `<library>/traits/` → `<library>/rules/`, and each doc's `# trait:` heading → `# rule:`.
     Docs the improver or the user added there ride along. A RETIRED slug (`_SLUG_MAP`) does
     not: its replacement rule ships in the seed, already generalized by hand, so carrying the
     old body over under a new name would undo exactly what this change made.
  2. Routine-LOCAL modules the improver authored into a single `traits/` dir, which the library
     never carried, are promoted into `rules/` — they are the only copy of that prose, and the
     drop that is correct for an adapted fork would be silent data loss here.
Per routine / conversation / background dir:
  3. its `traits/*.md` slugs → routine.yaml `rules:` (mapped — one retired slug may expand to
     SEVERAL rules where the old module conflated two principles — unknown slugs dropped), the
     directory deleted: the adapted copies are exactly what this change gives up, and keeping
     them would leave two sources of truth.
  4. `practice-library` dropped from `permissions:` and `read_trait` from
     `capabilities.actions` (the action is ungated now); `global-utils` added to
     `permissions:` for any routine that held the trait, since its prose became a
     conduct doc. The retired permission DOC is deleted from the library too, and every
     library WORKFLOW's `includes:` is rewritten through the same slug map — editing the
     seed does not touch a live instance (the sync only installs what is missing), so both
     would otherwise stay lint-red forever and keep seeding new routines with dead slugs.
  5. main.md's `## Standing practices` tail rebuilt from the new held set.
"""

from __future__ import annotations

import ast
import logging
import shutil
from pathlib import Path

import yaml

from . import libgit, library_docs, rules
from .config import ServerConfig
from .paths import atomic_write

log = logging.getLogger("rsched.migrate_rules")

# Retired trait slug → the general rule(s) that carry it now; () when nothing in the rules
# layer replaces it. A slug maps to SEVERAL rules where the retired module conflated two
# principles — the routine keeps both halves rather than silently losing one.
_SLUG_MAP: dict[str, tuple[str, ...]] = {
    # generalized: the principle, not LEDGER.md's filename/format/rotation
    "ledger-discipline": ("decision-record",),
    # became a PERMISSION (it is mechanism prose — a rule names no tool)
    "global-utils": (),
    # the REPORTING half is now a general rule; the instance's ownership TABLE moved into the
    # rules-review routine's recipe, which is the only place that needs it
    "maintenance-routing": ("problem-routing",),
    # two routine-local modules the improver authored, each conflating the same pair: what the
    # user WANTED (a standing preference) and WHY they had to say it (a defect with a cause)
    "correction-learning": ("root-cause-fix", "intent-inference"),
    "anticipatory-stewardship": ("root-cause-fix", "intent-inference"),
}

# Conduct docs retired with the rules layer. `read_trait` is gone from KINDS, so this doc's
# `requires:` can no longer normalize — it lints red forever until the file itself goes.
_RETIRED_PERMISSIONS = ("practice-library",)


def _migrate_library(server: ServerConfig) -> bool:
    old, new = server.libraries_home / "traits", server.rules_home
    if not old.is_dir():
        return False
    new.mkdir(parents=True, exist_ok=True)
    for src in sorted(old.glob("*.md")):
        targets = _SLUG_MAP.get(src.stem, (src.stem,))
        # a mapped slug's REPLACEMENT ships in the seed — never carry the retired body over
        # under a new name, or the generalization this change made would be undone on boot
        if src.stem not in _SLUG_MAP:
            for target in targets:
                dst = new / f"{target}.md"
                if not dst.exists():
                    body = src.read_text(encoding="utf-8").replace("# trait: ", "# rule: ", 1)
                    atomic_write(dst, body)
        src.unlink()
    shutil.rmtree(old, ignore_errors=True)
    libgit.commit(server.libraries_home, "migrate: traits/ -> rules/ (general rules)")
    return True


def _drop_retired_permissions(server: ServerConfig) -> int:
    """Delete conduct docs the rules layer retired, from the LIVE library.

    Removing one from `library-seed/` only stops fresh instances getting it; the seed sync
    never deletes (by design — it must not clobber a user's own docs), so an existing
    instance keeps the file forever. `practice-library` requires `read_trait`, which is no
    longer an action kind, so it also fails the library lint on every page load.
    """
    dropped = 0
    for slug in _RETIRED_PERMISSIONS:
        path = server.permissions_home / f"{slug}.md"
        if not path.is_file():
            continue
        path.unlink()
        log.warning("rules migration: deleted the retired permission doc %r from the library",
                    slug)
        dropped += 1
    if dropped:
        libgit.commit(server.libraries_home,
                      f"migrate: drop {dropped} retired permission doc(s)")
    return dropped


def _migrate_workflow_includes(server: ServerConfig) -> int:
    """Rewrite every LIBRARY workflow's `includes:` through the slug map.

    Same trap as the retired permission doc, one layer up and wider: editing a pattern in
    `library-seed/` does not reach a live instance (the seed sync only installs what is
    MISSING), and the library also carries patterns that were never in the seed at all —
    curator-drafted ones. Left alone, every one of them lints red ("include 'ledger-discipline'
    does not resolve") and, worse, seeds new routines with rule slugs that no longer exist.

    The list is located via the AST — these files are parsed, never executed — so the edit
    lands on the real literal regardless of how the pattern happens to be formatted.
    """
    from .workflows.library import workflows_dir

    wdir = workflows_dir(server.libraries_home)
    if not wdir.is_dir():
        return 0
    touched = 0
    for path in sorted(wdir.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        node = _includes_node(src)
        if node is None:
            continue
        old = [e.value for e in node.elts
               if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        new: list[str] = []
        for slug in old:
            new.extend(s for s in _SLUG_MAP.get(slug, (slug,)) if s not in new)
        if new == old:
            continue
        segment = ast.get_source_segment(src, node)
        if segment is None:
            continue
        rendered = "[" + ", ".join(f'"{s}"' for s in new) + "]"
        atomic_write(path, src.replace(segment, rendered, 1))
        log.warning("rules migration: %s includes %s -> %s", path.name, old, new)
        touched += 1
    if touched:
        libgit.commit(server.libraries_home,
                      f"migrate: retire mapped rule slugs from {touched} workflow include(s)")
    return touched


def _includes_node(src: str) -> ast.List | None:
    """The `includes:` list literal inside a pattern's META dict, or None."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if (isinstance(key, ast.Constant) and key.value == "includes"
                    and isinstance(value, ast.List)):
                return value
    return None


def _promote_orphans(server: ServerConfig, homes: tuple[Path, ...]) -> int:
    """Lift per-routine traits the library never carried into `rules/` — they are the ONLY
    copy of that prose.

    The routine-improver could author a module straight into a routine's own `traits/`, so a
    handful exist nowhere else. Every other per-routine file is an ADAPTED copy of a library
    trait and is meant to be dropped (that is the trade this change makes); an orphan dropped
    the same way would be silent data loss. A slug listed in `_SLUG_MAP` is NOT an orphan —
    its content was read and generalized into shipped rules by hand, so promoting the local
    copy would resurrect exactly what was replaced. First holder wins for the rest.
    """
    promoted = 0
    for home in homes:
        if not home.is_dir():
            continue
        for d in sorted(p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for src in sorted((d / "traits").glob("*.md")) if (d / "traits").is_dir() else []:
                if src.stem in _SLUG_MAP or (server.rules_home / f"{src.stem}.md").exists():
                    continue
                body = src.read_text(encoding="utf-8").replace("# trait: ", "# rule: ", 1)
                atomic_write(server.rules_home / f"{src.stem}.md", body)
                log.warning("rules migration: promoted %s's own %r into the shared library "
                            "(no library copy existed)", d.name, src.stem)
                promoted += 1
    if promoted:
        libgit.commit(server.libraries_home,
                      f"migrate: promote {promoted} routine-local rule(s) into the library")
    return promoted


def _held_rules(routine_dir: Path) -> tuple[list[str], bool]:
    """(mapped slugs, held the global-utils trait) from the routine's own traits/ dir."""
    tdir = routine_dir / "traits"
    if not tdir.is_dir():
        return [], False
    slugs = sorted(p.stem for p in tdir.glob("*.md"))
    mapped: list[str] = []
    for s in slugs:
        mapped.extend(r for r in _SLUG_MAP.get(s, (s,)) if r not in mapped)
    return mapped, "global-utils" in slugs


def _migrate_dir(server: ServerConfig, routine_dir: Path) -> bool:
    cfg_path = routine_dir / "routine.yaml"
    if not cfg_path.is_file() or not (routine_dir / "traits").is_dir():
        return False
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(raw, dict):
        return False
    held, had_global_utils = _held_rules(routine_dir)
    known = set(library_docs.slugs(server.rules_home))
    raw["rules"] = [s for s in held if s in known]

    perms = [p for p in (raw.get("permissions") or []) if p != "practice-library"]
    if had_global_utils and "global-utils" not in perms:
        perms.append("global-utils")
    raw["permissions"] = perms
    caps = raw.get("capabilities")
    if isinstance(caps, dict) and isinstance(caps.get("actions"), list):
        caps["actions"] = [a for a in caps["actions"] if a != "read_trait"]

    atomic_write(cfg_path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    shutil.rmtree(routine_dir / "traits", ignore_errors=True)
    rules.sync_practices_tail(routine_dir, server.rules_home)
    libgit.commit(routine_dir, "migrate: traits/ -> routine.yaml rules:")
    return True


def migrate_rules(server: ServerConfig) -> int:
    """Run the whole migration. Returns how many routine-shaped dirs were converted."""
    touched = 0
    homes = (server.routines_home, server.conversations_home, server.background_home)
    try:
        _migrate_library(server)
        _drop_retired_permissions(server)
        _migrate_workflow_includes(server)
        # BEFORE the per-dir conversion, which deletes traits/ — an orphan must be lifted
        # out while its only copy still exists.
        _promote_orphans(server, homes)
    except OSError as exc:
        log.warning("rules migration: library conversion failed: %s", exc)
        return 0
    for home in homes:
        if not home.is_dir():
            continue
        for d in sorted(p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                touched += _migrate_dir(server, d)
            except OSError as exc:
                log.warning("rules migration: %s failed: %s", d.name, exc)
    if touched:
        log.warning("migrated %d routine(s)/conversation(s) to library-global rules", touched)
    return touched
