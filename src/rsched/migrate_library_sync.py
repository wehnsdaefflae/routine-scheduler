"""MIGRATION(expires=2026-09-30): the library sync goes back to being a routine (0.165.0).

Publishing the instance to its library repo was a routine, was retired into a daemon job in
0.29.0, and is a routine again now. One piece of instance state still describes the daemon era
and breaks the new arrangement silently: `library_sync:` in config.yaml. The key no longer
exists on ServerConfig, and unknown top-level keys are reported as config problems on every
boot — a permanent warning about a setting nobody can act on.

Runs once at daemon boot, then gets deleted (the delete-after-convergence policy — CLAUDE.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .paths import atomic_write, config_file

log = logging.getLogger("rsched.migrate_library_sync")

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


def migrate_library_sync(server) -> bool:
    """Idempotent. True when the retired key was actually removed."""
    try:
        return _strip_config_key(server.source)
    except OSError as exc:
        log.warning("library-sync migration failed: %s", exc)
        return False
