"""MIGRATION(expires=2026-12-15): carry 0.345.0's `problem-routing` rewrite to the LIVE rule.

The seed sync (`bootstrap.sync_seed_library_docs`) is ADD-ONLY — it installs a rule the live
library is missing and never overwrites one — so a rule that already exists live cannot be
revised from `library-seed/` at all. This is the one-shot that carries one revision across, in
the shape `migrate_status_page_rule` established.

Why this rule and why now. Two of its bullets named operations that did not exist:

- "add your evidence to the OLDEST open one rather than opening another" (D110, shipped
  2026-08-31 as the fix for thread concentration) asked for an append the append-only ledger
  had no way to perform. The rule was obeyed by nobody because it could not be. Live count
  went from 28 to 50 in the eleven days after it shipped.
- routing a row to its owner left the original untargeted, so the next triage pass found it
  and routed it again — R1491/R1496/R1516/R1517/R1519/R1520 were each routed twice in two days
  (F492).

0.345.0 gives both an operation (`supersedes`), and this carries the prose that names it, plus
the `assists:` block that surfaces the receiving half at the moment a run is about to finish
owing someone a reply (D131).

Conservative the way every rule migration here is: it rewrites the live file ONLY when that
file is still byte-identical to the seed this revision replaced. An operator edit — or an
earlier partial migration — outranks the seed, and is named rather than passed over quietly.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger("rsched.migrate")

SLUG = "problem-routing"

#: sha256 of the seed text this revision replaces (the file as of 7a27d54). Compared against
#: the LIVE rule: identical means nobody has touched it since the seed installed it, which is
#: the only state this may overwrite. A hash and not the text itself — 40 lines of prose
#: embedded here would be a second copy of the rule for a reader to mistake for the real one.
PREVIOUS_SHA = "9a70612509cf38dafa32e0bb0fefdbec1b56d3dadbb1ee8c67bfea6759b1281c"


def migrate(rules_home: Path, seed_home: Path) -> list[str]:
    """Overwrite the live `problem-routing` with the seed's revision when it is untouched.
    Returns one log line describing what happened — nothing is silent.
    """
    live_path, seed_path = Path(rules_home) / f"{SLUG}.md", Path(seed_home) / f"{SLUG}.md"
    try:
        live = live_path.read_bytes()
        seed = seed_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{SLUG}: skipped — {exc}"]
    if hashlib.sha256(live).hexdigest() != PREVIOUS_SHA:
        already = "assists:" in live.decode("utf-8", "replace")
        return [f"{SLUG}: live rule is {'already revised' if already else 'locally edited'} — "
                "left alone"]
    try:
        live_path.write_text(seed, encoding="utf-8")
    except OSError as exc:
        return [f"{SLUG}: could not write — {exc}"]
    return [f"{SLUG}: revised (supersedes prose + pre-finish assist)"]


def run(rules_home: Path, seed_home: Path) -> int:
    """Daemon-boot entry point. Returns the number of rules changed; logs every decision."""
    notes = migrate(rules_home, seed_home)
    changed = sum(1 for n in notes if "revised" in n and "already" not in n)
    if changed:
        log.warning("problem-routing migration: %s", "; ".join(notes))
    return changed
