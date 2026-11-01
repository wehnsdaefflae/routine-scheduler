"""One-shot boot migration: the TRACKED `gu` dispatcher reaches the LIVE library —
MIGRATION(expires=2027-01-31).

`gu` is the dispatcher every `util` action ultimately invokes, and the reader of a util's
`net:`/`secrets:` header — the declarations that decide which network access and which
credentials fold into its sandbox. It was the one piece of that path carried by no repo: D225
made it `util-seed/gu`, so it has a test suite and the release gate like every other file here.

Carrying it to a live instance needs a migration, because `utils_lib.ensure_library` installs a
MINIMAL dispatcher (`utils_lib.DISPATCHER`: run a util, list the catalog) and
`_install_dispatcher` never overwrites an existing `gu` — by design, since a library root
carrying its own richer dispatcher must keep it. So an instance seeded from this repo has the
stub forever and gains nothing from the file becoming tracked.

The live copy is replaced ONLY while it is that stub, byte for byte, and the stub is KEPT beside
it (`gu.stub.bak`) and named in the record: a dispatcher is an executable an operator may have
edited, and this migration cannot tell an edited stub from an edited anything-else. Anything
that is not the stub — the richer dispatcher this seed came from, an operator's own, a deleted
one — is left exactly as it is and named, with the path to compare by hand.

Runs once at daemon boot, after the library exists and its seed syncs have run, and records what
it did in `.control/migrations/dispatcher-seed.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json
from .utils_lib import DISPATCHER

log = logging.getLogger("rsched.migrate_dispatcher_seed")

RECORD = Path(".control") / "migrations" / "dispatcher-seed.json"
#: The tracked dispatcher, repo-relative. `util-seed/` because that is where the live library's
#: code already comes from (`utils/` from `util-seed/utils/`) and ruff/the size ratchet already
#: treat that tree as single-file scripts gated by their own selftests, which this one now has.
SEED = Path("util-seed") / "gu"
#: Where the replaced stub is kept, in the library root beside the dispatcher itself — a backup
#: no one has to go looking for, and one the library's own git history then carries.
BACKUP = "gu.stub.bak"


def run_migration(server) -> dict:
    """Carry the tracked dispatcher once. Returns the record (empty when it already ran).

    Every attribute of `server` is read with `getattr`: the fresh-container boot test hands
    `cli_daemon.cmd_daemon` a stand-in carrying four fields, and a hard read here would fail the
    whole daemon boot for a question this migration was not asked.
    """
    from .bootstrap import repo_root

    routines_home = getattr(server, "routines_home", "")
    libraries_home = getattr(server, "libraries_home", "")
    if not routines_home or not libraries_home:
        return {}
    home, library = Path(routines_home), Path(libraries_home)
    record_path = home / RECORD
    if record_path.exists() or not home.is_dir() or not library.is_dir():
        return {}
    record: dict = {"started": now_iso()}
    live, seed = library / "gu", repo_root() / SEED
    try:
        current, new = live.read_bytes(), seed.read_bytes()
    except OSError as exc:      # a dispatcher deleted live stays deleted; a missing seed is a bug
        record["left"] = f"not read: {exc}"
    else:
        if current == new:
            record["left"] = "already current"
        elif current != DISPATCHER.encode():
            record["left"] = ("not the minimal seed dispatcher — compare it with util-seed/gu "
                              "and carry anything it has by hand")
        else:
            _replace(live, new, library / BACKUP)
            record["replaced"], record["backup"] = "gu", str(library / BACKUP)
            landed = libgit.commit(library,
                                   "gu: the tracked dispatcher replaces the minimal seed stub "
                                   f"(kept as {BACKUP}; util-seed/gu is the repo's copy)",
                                   routines_home=home, paths=["gu", BACKUP])
            if landed.failed:   # replaced on disk, not in the history: a person must look
                record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("gu dispatcher: %s",
                f"carried from util-seed/gu, stub kept as {BACKUP}" if record.get("replaced")
                else record.get("left"))
    return record


def _replace(live: Path, new: bytes, backup: Path) -> None:
    """Keep the stub, then install the tracked dispatcher over it.

    The backup is written FIRST, so a failure between the two leaves the stub both in place and
    saved. `+x` is forced on the result: the mode is copied from what was there, and a
    dispatcher that loses its executable bit is a dead instance.
    """
    atomic_write(backup, live.read_bytes(), mode=live.stat().st_mode)
    atomic_write(live, new, mode=live.stat().st_mode | 0o111)
