"""One-shot boot migration: a routine's `capabilities.utils` names only utils that EXIST —
MIGRATION(expires=2026-12-31).

THE SYMPTOM. Six installed routines open every run with two FAIL rows: *"held as a reserved
util ... no util by that name is in the library"* (`readmodels/surface_caps._absent_util_node`).
`birthday-admin`, `fau`, `freelance-hunter`, `grant-hunter`, `kaution-leipzig` and
`predatory-publisher-tarpit` each list `fau-mail:send` and `gmail:send` in
`capabilities.utils` beside the live `mail:send`. Nothing is broken — all six carry
`mail:send`, so no routine's sending is affected (measured 2026-11-01: 6 of 6). What is wrong
is that six routines teach their own runs to skim their preflight, which is worse than a dead
link.

THE CAUSE is the one a rename keeps reproducing: the `fau-mail`/`gmail` → `mail` consolidation
reached every CALLER's code and nothing reached the stores that hold util names as STRINGS.
`migrate_permission_requires` carries the narration of the other six symptoms found weeks apart
by different readers, and fixed the half that was the repo's: the `outbound-mail` permission's
`requires:` now names only `mail:send`, so the floor is correct and the remedy the surface
offers is performable. This file is the remaining half.

WHY A MIGRATION. `routine.yaml` is the operator's config and **no run may write it** — not even
the improver (`grants.CONFIG_FILE`, `GrantPolicy.deny`). So the only writer that can reach these
six files is the daemon's own boot, which is what this is. The operator authorized it in those
terms (D228, option (a), 2026-11-01): *"A one-shot boot migration drops the two dead verbs from
every installed routine.yaml's capabilities.utils, leaving mail:send."*

WHAT IT DOES NOT DO. It never ADDS a util: a file that would be left without `mail:send` is
dropped and NAMED in the record (`no_live_mail`) rather than quietly given a capability the
operator did not grant — a dead name grants nothing anyway, since the engine can only run a util
the library has. It is not a list of the six: every installed `routine.yaml` under the routine,
conversation and background homes is walked, so a seventh file written while this shipped does
not survive it. A file naming no dead util is left BYTE FOR BYTE, which is the hand-correction
guarantee this family of migrations rests on.

Written back with `paths.atomic_write_yaml`, the ONE writer this system has for `routine.yaml`
(key order preserved, unicode kept) — the same writer `migrate_total_turns` uses for the same
file. It is a full dump, so a comment would not survive
it; measured 2026-11-01, no `routine.yaml` on this instance carries a single comment line,
and nothing but this system's own writers has ever written one.

Runs once at daemon boot, before anything loads a routine, and records what it did in
`.control/migrations/dead-reserved-utils.json`; while that record exists it does nothing. The
drop is idempotent by construction as well: a second pass over a corrected file finds no dead
name and leaves it untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from . import libgit
from .ids import now_iso
from .paths import atomic_write_json, atomic_write_yaml, read_yaml

log = logging.getLogger("rsched.migrate_dead_reserved_utils")

RECORD = Path(".control") / "migrations" / "dead-reserved-utils.json"

#: The util NAMES the consolidation retired. Matched by NAME, not as the two literals
#: `fau-mail:send`/`gmail:send`: a bare `fau-mail` reserves every verb of a util that does not
#: exist either, and reports the same absent row. Neither name is in the 57-util catalog, so
#: nothing a routine can actually call is ever dropped here.
DEAD = ("fau-mail", "gmail")

#: The live verb the two were consolidated into — what the operator's answer leaves in place.
#: Only READ here (to notice a file left with no live send verb); never added.
LIVE = "mail:send"


def run_migration(server) -> dict:
    """Drop the dead names once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists() or not home.is_dir():
        return {}
    record: dict = {"started": now_iso(), "dropped": {}, "no_live_mail": [], "failed": {}}
    # The two extra homes are read with getattr, not as attributes: a boot migration is handed
    # whatever `load_server_config` returns, and `tests/test_bootstrap.py`'s fresh-container boot
    # hands it a stand-in with neither home — so a hard `server.conversations_home` makes the
    # WHOLE DAEMON BOOT die on an AttributeError in a migration that had nothing to do.
    # `migrate_total_turns` learned that on release gate 1792680349875750345. An absent home is
    # an absent home: nothing to walk.
    extra = [(label, getattr(server, f"{label}_home", None))
             for label in ("conversations", "background")]
    for label, root in [("routines", home), *[(a, b) for a, b in extra if b]]:
        for path in sorted(Path(root).glob("*/routine.yaml")) if Path(root).is_dir() else []:
            if path.parent.name.startswith("."):
                continue                        # clarify workspaces and strays stay untouched
            key = f"{label}/{path.parent.name}"
            try:
                _drop_one(path, key, record, home)
            except (OSError, yaml.YAMLError) as exc:
                # A file this migration cannot read is NAMED, never skipped silently: its two
                # FAIL rows stay, and the record is where a person sees that they stayed.
                record["failed"][key] = f"{type(exc).__name__}: {exc}"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("dead reserved utils: dropped from %s; no live %s left in %s; failed %s",
                record["dropped"] or "nothing", LIVE, record["no_live_mail"] or "nothing",
                record["failed"] or "nothing")
    return record


def _dead(entry: object) -> bool:
    """True when this `utils:` entry names a util the consolidation retired — bare or verbed."""
    from .grants import split_util_verb  # the house parser; imported here, not at boot

    return isinstance(entry, str) and split_util_verb(entry)[0] in DEAD


def _drop_one(path: Path, key: str, record: dict, home: Path) -> None:
    """Drop one file's dead `capabilities.utils` entries, or leave the file exactly as it is."""
    raw = read_yaml(path, {})
    caps = raw.get("capabilities") if isinstance(raw, dict) else None
    held = caps.get("utils") if isinstance(caps, dict) else None
    if not isinstance(caps, dict) or not isinstance(held, list):
        return                      # no `utils:` list: nothing this migration owns
    doomed = [entry for entry in held if _dead(entry)]
    if not doomed:
        # names no dead util: left byte for byte, hand-corrected or never wrong
        return
    caps["utils"] = [entry for entry in held if entry not in doomed]
    atomic_write_yaml(path, raw)
    record["dropped"][key] = doomed
    if LIVE not in caps["utils"]:
        # Never repaired by adding one: the operator's answer says drop, and a capability he did
        # not grant is not this migration's to hand out. Named so he can grant it himself.
        record["no_live_mail"].append(key)
    landed = libgit.commit(path.parent,
                           "capabilities: drop the reserved utils no library has "
                           f"({', '.join(doomed)}) — the fau-mail/gmail consolidation left them "
                           "FAILing at every run start (D228)",
                           routines_home=home, paths=["routine.yaml"])
    if landed.failed:   # dropped on disk, but not in its history: a person must look
        record["failed"][key] = f"dropped, but {landed.describe()}"
