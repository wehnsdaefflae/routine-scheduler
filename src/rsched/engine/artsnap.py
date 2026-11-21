"""Per-reply snapshots of a conversation's `artifacts/`, so ⟲ rewind restores the FILES too.

A routine dir is a git repo the engine autocommits; a CONVERSATION is not versioned, so without
this a rewind would restore the transcript and leave the files the discarded replies wrote.
Each reply's close-out copies `artifacts/` into `.artifact-snapshots/<run-ts>-t<turn>/`, and a
rewind restores the newest snapshot taken at or before the turn it kept.

A plain copy (not a diff) so it is recoverable BY HAND; inside the conversation so it travels
with it; dotted so the artifact listing never serves it.

- A restore snapshots the CURRENT state first (`pre-restore-…`); those are never pruned.
- Bounded to `KEEP` per-reply snapshots, oldest pruned.
- Best-effort: every entry point swallows OSError (logged) — a lost undo point never fails a
  reply.
- A reply that produced no files writes no snapshot.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("rsched.artsnap")

#: Where the snapshots live, inside the conversation. Dotted, so `api_conversations.artifacts`
#: (which lists `artifacts/`) never serves one as a deliverable.
SNAP_DIR = ".artifact-snapshots"

#: How many per-reply snapshots a conversation keeps: a rewind reaches for a RECENT reply, while
#: unbounded copies are a silent disk problem. A `pre-restore` copy is never pruned.
KEEP = 12


def _snapshots(conv_dir: Path) -> list[Path]:
    """Every per-reply snapshot, oldest first (not the `pre-restore-…` safety copies)."""
    root = conv_dir / SNAP_DIR
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()
                   and not p.name.startswith("pre-restore-")), key=lambda p: p.name)


def _turn_of(name: str) -> int:
    """The turn a snapshot name encodes (`<run-ts>-t<turn>`), or -1 when it encodes none."""
    _, _, tail = name.rpartition("-t")
    return int(tail) if tail.isdecimal() else -1


def take(conv_dir: Path, run_ts: str, turn: int) -> Path | None:
    """Snapshot `artifacts/` for the reply that just ended; None when there was nothing to keep.

    Named `<run-ts>-t<turn>` so it sorts chronologically and a restore can compare turns.
    Re-taking the same name overwrites it.
    """
    src = conv_dir / "artifacts"
    if not src.is_dir() or not any(src.iterdir()):
        return None            # the common case: a reply that wrote no deliverable
    dest = conv_dir / SNAP_DIR / f"{run_ts}-t{turn}"
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        prune(conv_dir)
        return dest
    except OSError as exc:      # a lost undo point must never fail the reply
        log.warning("artsnap: could not snapshot %s: %s", src, exc)
        return None


def prune(conv_dir: Path, keep: int = KEEP) -> int:
    """Drop the oldest per-reply snapshots beyond `keep`; returns how many went."""
    snaps = _snapshots(conv_dir)
    dropped = 0
    for old in snaps[:max(0, len(snaps) - keep)]:
        try:
            shutil.rmtree(old)
            dropped += 1
        except OSError as exc:
            log.warning("artsnap: could not prune %s: %s", old, exc)
    return dropped


def restore(conv_dir: Path, through_turn: int) -> dict | None:
    """Put `artifacts/` back to the newest snapshot taken at or before `through_turn`.

    Returns `{restored, from, kept}` (snapshot name, pre-restore copy name), or None when no
    snapshot covers that turn — not a failure, just nothing to put back. The CURRENT state is
    copied aside first.
    """
    candidates = [p for p in _snapshots(conv_dir) if 0 <= _turn_of(p.name) <= through_turn]
    if not candidates:
        return None
    snap = candidates[-1]
    live = conv_dir / "artifacts"
    kept = ""
    try:
        if live.is_dir() and any(live.iterdir()):
            backup = conv_dir / SNAP_DIR / f"pre-restore-{snap.name}"
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(live, backup)
            kept = backup.name
        if live.is_dir():
            shutil.rmtree(live)
        shutil.copytree(snap, live)
    except OSError as exc:
        log.warning("artsnap: could not restore %s: %s", snap, exc)
        return None
    return {"restored": True, "from": snap.name, "kept": kept}
