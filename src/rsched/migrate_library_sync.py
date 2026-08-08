"""MIGRATION(expires=2026-09-30): the library sync goes back to being a routine (0.165.0).

Publishing the instance to its library repo was a routine, was retired into a daemon job in
0.29.0, and is a routine again now. Two pieces of instance state still describe the daemon era
and would each break the new arrangement silently:

  1. `library_sync:` in config.yaml. The key no longer exists on ServerConfig, and unknown
     top-level keys are reported as config problems on every boot — a permanent warning about
     a setting nobody can act on.
  2. `<routines>/.archive/library-sync-retired/`. `bootstrap.adopt_seed_routine` treats an
     archived copy as "the user removed this on purpose" and matches by slug PREFIX, so that
     tombstone silently blocks the new `library-sync` routine from ever installing. It is
     renamed rather than deleted — it holds real run history from July 2026, and the point is
     to stop it matching, not to lose it.

Runs once at daemon boot, before seed adoption, then gets deleted (the delete-after-convergence
policy — CLAUDE.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .paths import atomic_write, config_file

log = logging.getLogger("rsched.migrate_library_sync")

RETIRED_DIR = "library-sync-retired"
# deliberately does NOT start with "library-sync" — that prefix is what blocks adoption
RENAMED_DIR = "daemon-era-instance-sync"


def _strip_config_key(path: Path | None = None) -> bool:
    path = path or config_file()
    if not path.is_file():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(raw, dict) or "library_sync" not in raw:
        return False
    raw.pop("library_sync")
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    log.warning("removed the retired library_sync: block from %s — the sync is a routine again",
                path)
    return True


def _clear_archive_tombstone(routines_home: Path) -> bool:
    archive = routines_home / ".archive"
    src = archive / RETIRED_DIR
    if not src.is_dir():
        return False
    dst = archive / RENAMED_DIR
    if dst.exists():
        return False
    src.rename(dst)
    log.warning("renamed %s -> %s so the library-sync routine can install (its run history "
                "is kept)", src, dst)
    return True


def migrate_library_sync(server) -> bool:
    """Both steps, independently idempotent. True when anything changed."""
    changed = False
    try:
        changed |= _strip_config_key(server.source)
        changed |= _clear_archive_tombstone(server.routines_home)
    except OSError as exc:
        log.warning("library-sync migration failed: %s", exc)
    return changed
