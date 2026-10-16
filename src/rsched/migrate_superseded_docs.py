"""One-shot boot migration: library docs this release rewrites reach the LIVE library whole —
MIGRATION(expires=2026-11-30).

- The six permissions that send AS THE OPERATOR gain the `outbox:` block the send gate
  enforces (`engine/sendgate.py`: his identity on the channel, the verbs that cannot send, the
  read every send owes), the `as-operator` tag, and the conduct line saying why.
- `interface-craft` gains its curated 0.404.0 body (docs/curated-rules.md has the sources) —
  carried whole here, BEFORE `migrate_rule_read_when` runs, so the new seed (which already
  says `read_when:`) lands instead of the old body with a key inserted.

`bootstrap.sync_seed_library_docs` only adds docs the live library is missing, so this carries
the change in the shape `migrate_seed_utils` uses: a live doc is replaced ONLY while it is
byte-identical to the seed this release supersedes. An operator's edit outranks the seed — it is
named in the record, with what to add by hand, rather than overwritten or passed over quietly.

Runs once at daemon boot, after the seed sync, and records what it did in
`.control/migrations/superseded-docs.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate_superseded_docs")

RECORD = Path(".control") / "migrations" / "superseded-docs.json"

#: library path → sha256 of the seed doc as of 0.403.0, the text this release supersedes. A
#: hash, not the text: seven whole docs here would be second copies a reader could mistake for
#: the real ones.
SUPERSEDED: dict[str, str] = {
    "permissions/outbound-mail.md":
        "e0f2ffec6d35e7f2c40658f50e3607c131c218305b25f8ceabfea90841c89601",
    "permissions/messaging-signal.md":
        "608c1ecdf417c6eb356ac4e44b84cab8a72759fcd56369d492b2cc066441d5de",
    "permissions/messaging-telegram.md":
        "a64060fed60f5fc9d389c0c319a9a86ccd777035b258bb364a318ee854115373",
    "permissions/messaging-whatsapp.md":
        "9ae6f322c4c91ec6cfccb9352110b61054379cf9da9a3df844bc5d33e2706d31",
    "permissions/messaging-zulip.md":
        "1f9c7e5e97b437cd1197d82f6be1f58fa9e524a3a8e08d61a94a3264ad356d4a",
    "permissions/usenet.md":
        "a0433dc0f1db5182272d4bb9bdabf0da154e587db92a602bca92c7ac8d6efe05",
    "rules/interface-craft.md":
        "a483dc92781409e18058a0eb48ae7c7bc5ea109391ac4a30a3de525d41dbb0d2",
}


def run_migration(server) -> dict:
    """Carry the docs once. Returns the record (empty when it already ran)."""
    from .bootstrap import repo_root

    home = Path(server.routines_home)
    record_path = home / RECORD
    library = Path(server.libraries_home)
    if record_path.exists() or not home.is_dir() or not library.is_dir():
        return {}
    seed = repo_root() / "library-seed"
    record: dict = {"started": now_iso(), "replaced": [], "left": {}}
    for rel, superseded in SUPERSEDED.items():
        verdict = _replace(library / rel, seed / rel, superseded)
        if verdict:
            record["left"][rel] = verdict
        else:
            record["replaced"].append(rel)
    if record["replaced"]:
        landed = libgit.commit(library,
                               "library: sends as the operator are gated on their outbox, and "
                               "interface-craft is re-curated (" + ", ".join(record["replaced"])
                               + ")",
                               routines_home=home, paths=list(record["replaced"]))
        if landed.failed:   # replaced on disk, not in the library's history: a person must look
            record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("superseded docs: replaced %s; left %s",
                ", ".join(record["replaced"]) or "none",
                "; ".join(f"{s} ({why})" for s, why in record["left"].items()) or "none")
    return record


def _replace(live: Path, seed: Path, superseded: str) -> str:
    """Install the seed doc over the live one. Returns "" on success, else why it was left."""
    try:
        new = seed.read_text(encoding="utf-8")
        current = live.read_text(encoding="utf-8")
    except OSError as exc:
        return f"unreadable: {exc}"
    if current == new:
        return "already current"
    if hashlib.sha256(current.encode("utf-8")).hexdigest() != superseded:
        return ("edited on this instance — carry the seed's changes across by hand "
                "(library-seed/ holds the new text)")
    try:
        atomic_write(live, new)
    except OSError as exc:
        return f"could not write: {exc}"
    return ""
