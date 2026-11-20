"""One-shot boot migration: `max_subruns` / `max_subrun_depth` become `max_child_runs` /
`max_child_depth` in every routine's and conversation's own config —
MIGRATION(expires=2026-12-31).

D241 increment 2. The keys are RENAMED, not aliased, for the reason the whole decision exists:
an alias is how two names for one thing survive indefinitely.

Why this migration is load-bearing rather than tidy. `config/routine_scrub` DROPS a budget key
it does not know, with a problem line — it does not crash. So a routine.yaml left saying
`max_subruns: 2` after the rename keeps validating, and its run silently takes the DEFAULT of 8
instead. A ceiling the operator deliberately lowered would quietly quadruple, and the only
evidence would be a problem line in a loader nobody reads. That is a worse failure than an
error, because nothing surfaces it.

What it touches: `budgets:` in every `routine.yaml` under the routines home, the conversations
home and the background home. Nothing else — a run's `state/`, its ledger and its transcripts
are the RECORD of what happened, and a run that was really capped at `max_subruns: 2` was.

It rewrites the FILE TEXT rather than loading and re-dumping the YAML, deliberately: a
routine.yaml is the operator's own document, with his comments and his ordering in it, and a
load/dump round trip discards every comment in the file. The keys are matched as whole words on
a `key:` line, so a comment that merely mentions the old name is left alone (the migration
record names every file it changed, so a stale mention is findable by hand).

Runs at daemon boot; records what it did in `.control/migrations/child-budgets.json`; while that
record exists it does nothing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "child-budgets.json"

#: old key -> new key. Longest first: `max_subrun_depth` contains no `max_subruns`, but keeping
#: the order explicit means a later pair cannot be shadowed by an earlier prefix.
RENAMES: tuple[tuple[str, str], ...] = (
    ("max_subrun_depth", "max_child_depth"),
    ("max_subruns", "max_child_runs"),
)


def _rewrite(text: str) -> tuple[str, int]:
    """The rewritten text and how many keys were renamed.

    Only a `key:` occurrence is renamed — the shape a budget entry actually has — so prose in a
    comment is not touched and a key cannot be rewritten inside a longer word.
    """
    count = 0
    for old, new in RENAMES:
        text, n = re.subn(rf"(?m)^(\s*){re.escape(old)}(\s*:)", rf"\1{new}\2", text)
        count += n
    return text, count


def _configs(server) -> list[Path]:
    """Every routine-shaped config on this instance: routines, conversations, background tasks."""
    out: list[Path] = []
    for home in (server.routines_home, getattr(server, "conversations_home", None),
                 getattr(server, "background_home", None)):
        if not home or not Path(home).is_dir():
            continue
        out.extend(d / "routine.yaml" for d in sorted(Path(home).iterdir())
                   if d.is_dir() and not d.name.startswith(".")
                   and (d / "routine.yaml").is_file())
    return out


def run_migration(server) -> dict:
    """Rename the two budget keys in every config that carries them. Returns the record (empty
    when it already ran, or when nothing carries them).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists():
        return {}

    changed: list[dict] = []
    for path in _configs(server):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new, count = _rewrite(text)
        if count and new != text:
            try:
                atomic_write(path, new)
            except OSError as exc:
                log.warning("child-budgets migration could not rewrite %s: %s", path, exc)
                continue
            changed.append({"path": str(path), "keys": count})

    record = {"ran": now_iso(), "files": len(changed), "changed": changed}
    atomic_write_json(record_path, record)
    if changed:
        log.info("child-budgets migration renamed the child-run keys in %d config(s) (D241)",
                 len(changed))
    return record
