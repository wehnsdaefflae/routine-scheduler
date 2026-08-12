"""MIGRATION(expires=2026-09-30): group members become records (F292, 0.181.0).

`.control/groups.json` stored members as plain slug strings; the two-phase group fire
gives each member a `split` flag, so the canonical membership shape is now a record
`{"slug": ..., "split": bool}`. The store's normalizer reads ONLY the record shape (no
dual-convention tolerance), so an existing instance's string members must be rewritten
once. Any pre-F292 in-flight chain file (`.control/group-runs/*.json` with string
members) is DELETED rather than converted: a chain is transient fire-progress state, the
member runs it already fired are each their own durable record, and the two-phase manager
cannot advance the old shape.

Runs once at daemon boot, then gets deleted (delete-after-convergence — CLAUDE.md).
"""

from __future__ import annotations

import logging

from .group_runs import runs_dir
from .groups import groups_file
from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.migrate_group_members")


def migrate_group_members(server) -> bool:
    """Rewrite string members to records; drop old-shape in-flight chains. True on change."""
    changed = False
    path = groups_file(server.routines_home)
    raw = read_json(path)
    if isinstance(raw, dict):
        converted = 0
        for g in raw.get("groups") or []:
            if not isinstance(g, dict):
                continue
            members = g.get("members") or []
            if any(isinstance(m, str) for m in members):
                g["members"] = [{"slug": m, "split": False}
                                for m in members if isinstance(m, str) and m.strip()]
                converted += 1
        if converted:
            atomic_write_json(path, raw)
            log.warning("groups.json: converted %d group(s) to member records", converted)
            changed = True
    d = runs_dir(server.routines_home)
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            rec = read_json(f)
            members = rec.get("members") if isinstance(rec, dict) else None
            if isinstance(members, list) and any(isinstance(m, str) for m in members):
                f.unlink(missing_ok=True)
                log.warning("dropped pre-F292 in-flight chain %s (old member shape)", f.name)
                changed = True
    return changed
