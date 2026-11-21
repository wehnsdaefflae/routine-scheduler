"""One-shot boot migration: stored transcripts carry `child_start` / `child_end`
— MIGRATION(expires=2026-12-31).

D241 (the operator in conversation: *"sub-runs / subtasks (why two terms for the same thing? fix
that)"*) renames the two child-run transcript events from `subrun_start` / `subrun_end`. The
kinds and the budget keys shipped in increments 1 and 2; this is the event vocabulary, and the
vocabulary is a CONTRACT rather than a statement of fact.

WHY A TRANSCRIPT IS REWRITTEN HERE, when the earlier D241 migrations deliberately did not touch
one. Those rewrote PROSE — a recipe saying "spawn a child" — and left every transcript alone,
because a record saying a run called `subtask` in October is TRUE and a ledger may not lie about
the past. An event `type` is not a sentence about the past: it is the token three live readers
dispatch on, and all three fail SILENTLY on a token they do not know.
`static/components/transcript.js` DROPS an unrendered type, so a past run's page simply loses
those rows; `readmodels/subruntree.py` matches the two names literally, so the rail's
decomposition tree of every pre-rename run renders empty; `engine/history.py` rebuilds a
resumed leg's child announcements and its orphan set from `child_end`, so a resume that
re-reads an unmigrated transcript understates the tree it inherits (the shape R3112 already
cost once). Nothing raises, nothing logs — the surfaces just quietly hold less.

WHAT IT REWRITES, and nothing else: the `type` FIELD of a transcript event, on a line that
parses as a JSON object, in `transcript.jsonl` and its retention-gzipped `transcript.jsonl.gz`,
under every run directory of every routine, conversation and background task — the whole run
TREE, since a child's own transcript records its children (`sub/<n>/sub/<m>/`). Prose is never
touched: a `say` that mentions "subrun_end", an observation quoting an old event name, a
summary — all survive byte-for-byte, because the rewrite is a key assignment on a parsed object
and not a text substitution. A line that does not parse is copied through unchanged; a file
that cannot be read or written is left alone and counted.

Runs at daemon boot; records what it changed in `.control/migrations/child-events.json`; while
that record exists it does nothing.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "child-events.json"

#: The rename, old → new. Only these two tokens, and only as a whole `type` value.
RENAMES = {"subrun_start": "child_start", "subrun_end": "child_end"}

#: The cheap pre-filter: a file without one of these bytes anywhere cannot need rewriting, and
#: most of this instance's transcripts do not. Parsing 330 MiB of JSONL at every boot would be
#: the whole cost of the migration; this way the parse happens only on the files that match.
_NEEDLES = tuple(f'"{old}"'.encode() for old in RENAMES)


def _rewrite_text(text: str) -> tuple[str, int]:
    """The migrated JSONL and how many event rows changed.

    Line-wise and parse-based: a row is rewritten only where `type` IS one of the old names.
    Everything else — a malformed line, a blank, prose that merely mentions the old name —
    is emitted exactly as it came in.
    """
    out: list[str] = []
    changed = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        try:
            ev = json.loads(stripped)
        except (ValueError, TypeError):
            out.append(line)
            continue
        old = ev.get("type") if isinstance(ev, dict) else None
        new = RENAMES.get(old) if isinstance(old, str) else None
        if new is None:
            out.append(line)
            continue
        ev["type"] = new
        eol = "\n" if line.endswith("\n") else ""
        out.append(json.dumps(ev, ensure_ascii=False) + eol)
        changed += 1
    return "".join(out), changed


def _migrate_file(path: Path) -> int:
    """Rewrite one transcript in place; return the event rows changed, or -1 if it could not be.

    Returns 0 — and leaves the file untouched — when nothing matches or the rewrite would
    produce no change. The write replaces the file in place rather than going through
    `atomic_write`, because a gzipped archive must keep its own compression and a plain
    transcript may be APPENDED to by a live run between the read and the write: boot, before
    any engine starts, is the one moment when that cannot happen, which is why this runs there.
    """
    gz = path.suffix == ".gz"
    try:
        raw = gzip.decompress(path.read_bytes()) if gz else path.read_bytes()
    except (OSError, ValueError, EOFError) as exc:
        log.warning("child-events migration could not read %s: %s", path, exc)
        return -1
    if not any(needle in raw for needle in _NEEDLES):
        return 0
    try:
        text, changed = _rewrite_text(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        log.warning("child-events migration could not decode %s: %s", path, exc)
        return -1
    if not changed:
        return 0
    data = text.encode("utf-8")
    try:
        path.write_bytes(gzip.compress(data) if gz else data)
    except OSError as exc:
        log.warning("child-events migration could not write %s: %s", path, exc)
        return -1
    return changed


def _transcripts(home: Path) -> list[Path]:
    """Every transcript under one home, at every depth of every run tree."""
    if not home.is_dir():
        return []
    found: list[Path] = []
    for name in ("transcript.jsonl", "transcript.jsonl.gz"):
        found.extend(p for p in home.rglob(name) if p.is_file())
    return found


def run_migration(server) -> dict | None:
    """Rename the two child-run events in every stored transcript, once.

    Returns the record it wrote, or None when the record already exists.
    """
    record_path = Path(server.routines_home) / RECORD
    if record_path.exists():
        return None
    # Read with getattr and fail open: this runs at BOOT, before the app exists, and the boot
    # sequence is exercised with minimal server stand-ins that supply only the homes the step
    # under test needs. A migration that raises there would take the whole daemon down over a
    # home it had nothing to do — a home that is absent simply holds no transcripts.
    homes = [Path(h) for h in (getattr(server, "routines_home", None),
                               getattr(server, "conversations_home", None),
                               getattr(server, "background_home", None)) if h]
    files = 0
    rows = 0
    unreadable: list[str] = []
    for home in homes:
        for path in _transcripts(home):
            n = _migrate_file(path)
            if n < 0:
                unreadable.append(str(path))
            elif n:
                files += 1
                rows += n
    record = {"ts": now_iso(), "files_changed": files, "events_renamed": rows,
              "unreadable": unreadable, "renames": RENAMES}
    atomic_write_json(record_path, record)
    log.info("child-events migration: %d events renamed in %d transcripts (%d unreadable)",
             rows, files, len(unreadable))
    return record
