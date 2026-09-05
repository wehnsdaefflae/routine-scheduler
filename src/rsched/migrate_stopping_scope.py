"""MIGRATION(expires=2026-11-05): stopping conditions gain a SCOPE; run bounds stop sticking.

Two things were true at once and could not both be right. `record_accounting` made a `met`
condition STICKY, deliberately — the user should not be told twice that a goal is done. And the
0.286.x backfill wrote 96 conditions across 32 routines as PER-RUN bounds ("the site was
published", "exactly one bounded increment was produced"), deliberately — because the finish gate
demands an accounting for every active condition, and a project milestone would report `unmet`
forever and train the model to write noise.

Sticky + per-run is the defect. A run bound is satisfied by its own run and then stays satisfied,
so `active()` drops it, the finish gate stops demanding it, the verifier stops checking it — and
the digest opens with "EVERY stopping condition is now met — the job is DONE. Finish NOW" on every
subsequent run, forever. **22 of the 31 live routines were in that state**, including self-audit,
which ran 271 of its 300 turns straight through that sentence.

The fix is the scope: a `run` condition never transitions, a `goal` condition does. This migration
brings the live stores to it, and it can only be done ONE way — every existing condition is a RUN
bound, because that is what the backfill wrote:

1. `scope: "run"` on every condition that does not already declare one.
2. Every `met` run condition back to `open`. Nothing is lost: the verdict is preserved in
   `last_verdict` (new, engine-owned) alongside the `note`, `resolved_run` and `resolved_ts` that
   were already there, and the digest renders it as "last run: met — <note>".
3. `dropped` is left alone — that is the user retiring a condition, not a run satisfying one.

No condition is PROMOTED to `goal` here. Two routines (aisafety-grant-steward, miz-grant-steward)
do carry milestone-shaped conditions, but which routines have a terminal goal — and in whose words
— is the user's call, made in the panel, and a migration that guessed would be writing the user's
own document for them.

Runs once at daemon boot, then gets deleted (delete-after-convergence — CLAUDE.md).
"""

from __future__ import annotations

import logging

from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.migrate_stopping_scope")


def _migrate_doc(raw: dict) -> bool:
    """Convert one loaded document in place. True when anything changed."""
    conditions = raw.get("conditions")
    if not isinstance(conditions, list):
        return False
    changed = False
    for c in conditions:
        if not isinstance(c, dict):
            continue
        if not c.get("scope"):
            c["scope"] = "run"
            changed = True
        if c["scope"] == "run" and c.get("status") == "met":
            c["status"] = "open"
            c.setdefault("last_verdict", "met")
            changed = True
    return changed


def migrate_stopping_scope(server) -> int:
    """Convert every stopping document in every home. Returns how many were rewritten."""
    changed = 0
    for home in (server.routines_home, server.conversations_home, server.background_home):
        if not home or not home.is_dir():
            continue
        for d in sorted(home.iterdir()):
            store = d / "state" / "stopping.json"
            if not d.is_dir() or d.name.startswith(".") or not store.is_file():
                continue
            raw = read_json(store)
            if not isinstance(raw, dict) or not _migrate_doc(raw):
                continue
            atomic_write_json(store, raw)
            log.warning("stopping-scope migration: %s — conditions are RUN bounds again", d.name)
            changed += 1
    return changed
