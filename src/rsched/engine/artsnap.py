"""Per-reply snapshots of a conversation's `artifacts/`, so ⟲ rewind restores the FILES too.

A scheduled routine's directory is a git repo the engine autocommits at every run end, so its
deliverables have a history and a run can be undone from it. A CONVERSATION is not versioned:
its `artifacts/` holds exactly what the newest reply left there. So ⟲ rewind — which exists to
take a conversation back to a chosen turn — put the TRANSCRIPT back and left the files as the
discarded replies had written them. The conversation then said one thing and its deliverables
said another, which is the shape of error nobody notices until they act on the wrong file.

So each reply's close-out copies `artifacts/` into `.artifact-snapshots/<run-ts>-t<turn>/`, and
a rewind restores the newest snapshot taken at or before the turn it kept.

## Why a copy rather than a diff, and why under the conversation

A copy is recoverable BY HAND. The whole point of a snapshot is the moment the automation is
wrong, and a reader who finds `.artifact-snapshots/20261110-140003-t7/report.md` can act on it
with no tool at all — which a pack file or a diff chain does not give. The directory sits inside
the conversation so a copied or archived conversation carries its own history, and it starts with
a dot so it stays out of the artifact listing the console serves.

## What it will NOT do

- **It never deletes anything a reply wrote without keeping it.** A restore snapshots the CURRENT
  state first (`pre-restore-…`), because a rewind is itself an action someone may regret.
- **It is bounded.** `KEEP` snapshots are retained, oldest pruned — a conversation with 200
  replies and a 40 MB artefact would otherwise grow by 8 GB. The bound is per conversation, and
  pruning never touches a `pre-restore` copy, which is the one a person goes looking for.
- **It is best-effort and never fails a reply.** A snapshot that cannot be written is a lost
  undo point, which is a far smaller harm than a reply that fails to finish: every entry point
  swallows OSError and says so in the log.
- **It holds no empty directories.** A conversation whose replies produce no files writes no
  snapshots at all, so the common case costs nothing.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("rsched.artsnap")

#: Where the snapshots live, inside the conversation. Dotted, so `api_conversations.artifacts`
#: (which lists `artifacts/`) never serves one as a deliverable.
SNAP_DIR = ".artifact-snapshots"

#: How many per-reply snapshots a conversation keeps. Chosen for the shape of the risk rather
#: than measured: a rewind reaches for a RECENT reply (the point of ⟲ is "that last answer went
#: wrong"), while unbounded copies of a large artefact set are a disk problem that arrives
#: silently. A `pre-restore` copy is never pruned.
KEEP = 12


def _snapshots(conv_dir: Path) -> list[Path]:
    """Every per-reply snapshot, oldest first. `pre-restore-…` copies are not among them: they
    are a person's safety net, not a point to roll back to.
    """
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

    Named `<run-ts>-t<turn>` so it sorts chronologically and a restore can compare its turn
    against the turn a rewind kept. Re-taking the same name overwrites it, which is what a
    re-run of the same reply should do.
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

    Returns `{restored, from, kept}` — the snapshot's name, and the name of the pre-restore copy
    of what was there — or None when no snapshot covers that turn (a conversation that produced
    no files, or one whose history has been pruned past it). None is NOT a failure: there is
    simply nothing to put back, and the caller says so rather than claiming a restore.

    The CURRENT state is copied aside first. A rewind is itself an act someone may regret, and
    the files a discarded reply wrote may be the ones that mattered.
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
