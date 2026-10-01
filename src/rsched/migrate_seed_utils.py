"""One-shot boot migration: this release's seed-util fixes reach the LIVE library —
MIGRATION(expires=2026-11-15).

`bootstrap.sync_seed_utils` installs a seed util the live library is MISSING and never touches
one it already has, so a fix to an existing util reaches nobody from `util-seed/` (0.371.1
carried the previous batch across by hand). This carries four, in the shape the rule
migrations established (`migrate_problem_routing_rule`, 0.345.0): a live util's `main.py` is
replaced ONLY while it is byte-identical to the seed this release supersedes. A routine's
`write_util` revision or an operator's edit outranks the seed — it is named in the record, with
the path to apply by hand, rather than overwritten or passed over quietly.

Runs once at daemon boot, after the seed sync, and records what it did in
`.control/migrations/seed-utils.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate_seed_utils")

RECORD = Path(".control") / "migrations" / "seed-utils.json"

#: util → (sha256 of the seed `main.py` this release supersedes — the file as of 0.371.1 —
#: and what the new one fixes). A hash and not the text: four whole utils embedded here
#: would be second copies for a reader to mistake for the real ones.
SUPERSEDED: dict[str, tuple[str, str]] = {
    "remote": ("82a24319cde86edb7ef698f787f9761c2aec1d726c79b0b279ed11e12e18c17f",
               ("an exclusive machine's queue in the box's own order; a host scan that "
                "cannot connect fails at once")),
    "vision": ("3af24bdb0384371f76eba69c56e8b2c0d79665cc347a21b3c387053ca8d0d9e4",
               "a rate-limited call reports inside its deadline"),
    "git": ("b100a0320fefc7f03ff9d8bff15c3ab3748f3cb1230ee41f21da1912793f50bc",
            ("works in a linked worktree or submodule; its commit lock never lands in a "
             "work tree")),
    "reminder-census": ("bc7b1928affcd52ff7c07decb210421f1b395b102be203acb6764235361197b1",
                        "reads a multi-image view_image call the way the engine renders it"),
}


def run_migration(server) -> dict:
    """Carry the fixes once. Returns the record (empty when it already ran)."""
    from .bootstrap import repo_root

    home = Path(server.routines_home)
    record_path = home / RECORD
    utils = Path(server.libraries_home) / "utils"
    if record_path.exists() or not home.is_dir() or not utils.is_dir():
        return {}
    seed = repo_root() / "util-seed" / "utils"
    record: dict = {"started": now_iso(), "replaced": {}, "left": {}}
    for name, (superseded, fixes) in SUPERSEDED.items():
        verdict = _replace(utils / name / "main.py", seed / name / "main.py", superseded)
        if verdict:
            record["left"][name] = verdict
        else:
            record["replaced"][name] = fixes
    if record["replaced"]:
        landed = libgit.commit(Path(server.libraries_home),
                               "seed utils: " + ", ".join(record["replaced"]) + " carry this "
                               "release's fixes (the seed sync only adds missing utils)",
                               routines_home=home,
                               paths=[f"utils/{name}/main.py" for name in record["replaced"]])
        if landed.failed:   # replaced on disk, not in the library's history: a person must look
            record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("seed utils: replaced %s; left %s",
                ", ".join(record["replaced"]) or "none",
                "; ".join(f"{n} ({why})" for n, why in record["left"].items()) or "none")
    return record


def _replace(live: Path, seed: Path, superseded: str) -> str:
    """Overwrite `live` with `seed` when it is the superseded seed byte for byte. Returns ""
    when replaced, else why it was left as it is.
    """
    try:
        current, new = live.read_bytes(), seed.read_bytes()
    except OSError as exc:      # a util deleted live stays deleted; a missing seed is a bug
        return f"not read: {exc}"
    if current == new:
        return "already current"
    if hashlib.sha256(current).hexdigest() != superseded:
        return (f"edited since it was seeded — compare it with util-seed/utils/{live.parent.name}"
                "/main.py and carry the fix by hand")
    atomic_write(live, new, mode=live.stat().st_mode)
    return ""
