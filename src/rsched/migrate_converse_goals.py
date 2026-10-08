"""One-shot boot migration: the converse pattern's GOALS revision reaches the LIVE library —
MIGRATION(expires=2026-11-30).

`bootstrap.sync_seed_library_docs` installs a seed workflow the live library is MISSING and never
touches one it already has, so version 5 of `converse` — a conversation keeps what the user asked
for as goals, and every reply declares whether it is final — would reach nobody from
`library-seed/`. The live `workflows/converse.py` is replaced ONLY while it is byte-identical to
the version-4 seed it supersedes; an edit made since outranks the seed and is named in the record,
with the path to carry by hand, rather than overwritten.

A conversation materializes the pattern when it is CREATED, so this reaches new conversations. An
existing one keeps its version-4 copy, which stays true for it: it was created with per-reply
ceilings, which version 4 describes, and it learns the goal contract from the harness and the
`goal` kind like every run.

Runs once at daemon boot, after the seed sync, and records what it did in
`.control/migrations/converse-goals.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate_converse_goals")

RECORD = Path(".control") / "migrations" / "converse-goals.json"
PATTERN = Path("workflows") / "converse.py"
#: sha256 of the version-4 seed `converse.py` (as of 0.394.0) this release supersedes — a hash
#: and not the text, so no second copy of the pattern sits here for a reader to mistake.
SUPERSEDED = "1965955e50c5d41ed627b6793a87f355aef5f03395c4463e3013c611c17b18e2"


def run_migration(server) -> dict:
    """Carry the revision once. Returns the record (empty when it already ran)."""
    from .bootstrap import repo_root

    home = Path(server.routines_home)
    record_path = home / RECORD
    library = Path(server.libraries_home)
    if record_path.exists() or not home.is_dir() or not (library / "workflows").is_dir():
        return {}
    record: dict = {"started": now_iso()}
    live, seed = library / PATTERN, repo_root() / "library-seed" / PATTERN
    try:
        current, new = live.read_bytes(), seed.read_bytes()
    except OSError as exc:      # a pattern deleted live stays deleted; a missing seed is a bug
        record["left"] = f"not read: {exc}"
    else:
        if current == new:
            record["left"] = "already current"
        elif hashlib.sha256(current).hexdigest() != SUPERSEDED:
            record["left"] = ("edited since it was seeded — compare it with library-seed/"
                              "workflows/converse.py and carry the goals revision by hand")
        else:
            atomic_write(live, new)
            record["replaced"] = str(PATTERN)
            landed = libgit.commit(library, "converse: keep the user's goals and declare every "
                                            "reply final or not (version 5; the seed sync only "
                                            "adds missing patterns)",
                                   routines_home=home, paths=[str(PATTERN)])
            if landed.failed:   # replaced on disk, not in the library's history: a person looks
                record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("converse pattern: %s",
                "carried to version 5" if record.get("replaced") else record.get("left"))
    return record
