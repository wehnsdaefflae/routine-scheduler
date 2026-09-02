"""Conversation branching — the `branch` scheduling mode of the CHILD RUN contract (F325).

A **branch** forks a conversation at a chosen turn into a NEW conversation whose `parent`
records the origin slug and the fork point. It starts from the parent's config (models,
permissions, capabilities, rules, connections, roots, budgets, deliberation) and a COPY of the
transcript up to the fork point — so it reasons with the same history and **cannot mutate the
original**. Two divergent lines of work, both live, neither able to damage the other.

It is the `branch` mode of `engine/child.py`'s contract, and obeys all three parts:

- **Isolation** — its own directory, its own transcript. Nothing it does reaches the parent
  except through the hand-back below.
- **A budget of its own** — a conversation's budgets are per-reply, and the branch gets the
  parent's ceilings as its own; it never draws down the parent's.
- **A declared hand-back** — `hand_back()`, below.

Unlike `spawn`/`subtask`, the branch is driven by the USER rather than by the engine: it is a
conversation, so it advances when someone writes in it. That is the only thing the mode changes.

**Merging is deliberately NOT a transcript merge.** Two divergent histories cannot be
interleaved into one coherent conversation — the result would be a record of a conversation
that never happened. Merging is a HAND-BACK, exactly the child-run result: the branch delivers
a summary plus its artefacts into the parent as a message and files, the way a detached
background task delivers (`daemon/detached.py`). The parent then chooses what to do with them.

What is copied and what is not, and why:

- `main.md`, `instruction.md`, `tuning.yaml`, `state/`, `attachments/` — copied. The branch must
  reason with the same recipe, the same working plan and the same files its inherited history
  refers to; a transcript mentioning `attachments/x.png` with no such file is a broken history.
- `artifacts/` — NOT copied. Artefacts are what a conversation HANDS OVER, and the branch's job
  is to produce its own and hand those back. Copying the parent's would make every hand-back
  return the parent its own files.
- Per-event `usage` on the copied transcript — STRIPPED. The parent already accounted for that
  spend; the branch's meters must report what the BRANCH cost, or the same tokens are counted
  twice across two conversations. The events' content is untouched: usage is telemetry, not part
  of what was said.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .engine.history import cut_index_for_turn
from .engine.transcript import read_events
from .ids import now_iso, run_ts
from .paths import atomic_write, atomic_write_json, atomic_write_yaml, read_yaml

if TYPE_CHECKING:
    from .config import ServerConfig

log = logging.getLogger("rsched.branches")

# Where a branch's hand-back lands in the parent — namespaced by the branch slug, mirroring the
# child-run `from-sub-<n>/` and the detached task's `from-bg-<id>/`.
HANDBACK_PREFIX = "from-branch-"

# Copied wholesale so the branch's inherited history still resolves. `artifacts/` is
# deliberately absent — see the module docstring.
COPIED_TREES = ("state", "attachments")
COPIED_FILES = ("main.md", "instruction.md", "tuning.yaml")


def branch_slug(conversations_home: Path, parent_slug: str) -> str:
    """`<parent>-b<n>`, first free n. The lineage stays readable in the directory name itself,
    which matters when a branch is later found on disk with no UI in front of it.
    """
    n = 1
    while (conversations_home / f"{parent_slug}-b{n}").exists():
        n += 1
    return f"{parent_slug}-b{n}"


def _strip_usage(event: dict) -> dict:
    """One copied transcript event, without its telemetry. See the module docstring."""
    return {k: v for k, v in event.items() if k != "usage"}


def fork_conversation(server: ServerConfig, *, parent_dir: Path, parent_slug: str,
                      at_turn: int, name: str = "") -> dict:
    """Fork `parent_slug` at `at_turn` into a new conversation. Returns
    `{slug, dir, at_turn, kept_events}`.

    The fork point snaps to a clean TURN boundary — through the assistant action of `at_turn`
    and the observation that answered it — using the same cut the D69 rewind uses, so a branch
    can never start from half a turn. Raises ValueError when the parent has no run yet or the
    turn is not in its transcript.
    """
    runs = sorted((d for d in (parent_dir / "runs").iterdir() if d.is_dir()),
                  key=lambda d: d.name, reverse=True) if (parent_dir / "runs").is_dir() else []
    if not runs:
        raise ValueError("this conversation has not run yet — there is nothing to branch from")
    events, _ = read_events(runs[0] / "transcript.jsonl", 0)
    cut = cut_index_for_turn(events, at_turn)
    if cut is None:
        raise ValueError(f"turn {at_turn} is not in this conversation's transcript")

    slug = branch_slug(server.conversations_home, parent_slug)
    branch_dir = server.conversations_home / slug
    for sub in ("state", "inbox", "attachments", "artifacts"):
        (branch_dir / sub).mkdir(parents=True, exist_ok=True)
    for tree in COPIED_TREES:
        src = parent_dir / tree
        if src.is_dir():
            shutil.copytree(src, branch_dir / tree, dirs_exist_ok=True)
    for fname in COPIED_FILES:
        src = parent_dir / fname
        if src.is_file():
            shutil.copy(src, branch_dir / fname)

    raw = read_yaml(parent_dir / "routine.yaml", {})
    parent_name = str(raw.get("name") or parent_slug)
    raw["name"] = name.strip() or f"{parent_name} (branch)"
    raw["description"] = raw["name"]
    # The provenance the whole feature hangs on: which conversation, and where it split.
    raw["parent"] = {"slug": parent_slug, "turn": at_turn, "forked": now_iso()}
    atomic_write_yaml(branch_dir / "routine.yaml", raw)

    ts = run_ts()
    run_dir = branch_dir / "runs" / ts
    run_dir.mkdir(parents=True)
    kept = [_strip_usage(e) for e in events[: cut + 1]]
    # The header names the run — copying the parent's verbatim would leave the branch's
    # transcript claiming to be the parent's run, which every read model keys off.
    kept[0] = {**kept[0], "run_id": f"{slug}:{ts}", "routine": slug,
               "branched_from": {"slug": parent_slug, "turn": at_turn}}
    atomic_write(run_dir / "transcript.jsonl",
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept))
    # A TERMINAL status is what makes the branch resumable: the user's first message in it goes
    # down the ordinary `resume_terminal` path and replays this transcript, so a branch is a
    # continued conversation from turn one rather than a special case in the engine.
    atomic_write_json(run_dir / "status.json",
                      {"state": "finished", "outcome": "ok", "turn": at_turn,
                       "started": now_iso(), "updated": now_iso(), "usage": {"in": 0, "out": 0},
                       "elapsed_s": 0})
    atomic_write(run_dir / "result.md",
                 f"Branched from **{parent_name}** (`{parent_slug}`) at turn {at_turn}. "
                 f"The history up to that point is this conversation's; anything after it is "
                 f"the other branch's and is not visible here.")
    log.info("branch: forked %s at turn %d -> %s (%d events)", parent_slug, at_turn, slug,
             len(kept))
    return {"slug": slug, "dir": branch_dir, "at_turn": at_turn, "kept_events": len(kept)}


def _handback_text(*, branch_slug_: str, branch_name: str, summary: str, copied: int) -> str:
    lines = [f"[branch handed back] The branch “{branch_name}” ({branch_slug_}) handed its "
             "result back to you.", "", summary or "(no summary was written.)"]
    if copied:
        lines += ["", f"Its {copied} artefact(s) were copied to "
                      f"`artifacts/{HANDBACK_PREFIX}{branch_slug_}/`."]
    lines += ["", "This is a hand-back, not a merge: the branch's conversation stays its own. "
                  "Take what is useful from the summary and the files, and tell me what you "
                  "make of it."]
    return "\n".join(lines)


def hand_back(server: ServerConfig, *, branch_dir: Path, slug: str, summary: str) -> dict:
    """Deliver a branch's result to its parent: artefacts copied into the parent's
    `artifacts/from-branch-<slug>/`, and one inbox message carrying the summary and naming
    them. Returns `{parent, copied, message}`.

    Exactly the shape a detached background task delivers in (`daemon/detached._deliver_one`) —
    that is the point: a hand-back is the child-run result, and the parent already knows how to
    read one. Delivery does NOT wake the parent; its next reply drains the message, the way
    every other inbox message reaches a conversation. Raises ValueError when the conversation is
    not a branch or its parent is gone.
    """
    raw = read_yaml(branch_dir / "routine.yaml", {})
    parent = raw.get("parent") or {}
    parent_slug = str(parent.get("slug") or "")
    if not parent_slug:
        raise ValueError("this conversation is not a branch — it has no parent to hand back to")
    parent_dir = server.conversations_home / parent_slug
    if not (parent_dir / "routine.yaml").is_file():
        raise ValueError(f"the parent conversation {parent_slug!r} no longer exists")

    copied = 0
    src = branch_dir / "artifacts"
    if src.is_dir() and any(src.iterdir()):
        dst = parent_dir / "artifacts" / f"{HANDBACK_PREFIX}{slug}"
        # namespaced + overwrite: never clobber the parent's own artefacts, and idempotent when
        # a branch hands back more than once as it goes.
        shutil.copytree(src, dst, dirs_exist_ok=True)
        copied = sum(1 for p in dst.rglob("*") if p.is_file())

    text = _handback_text(branch_slug_=slug, branch_name=str(raw.get("name") or slug),
                          summary=summary, copied=copied)
    atomic_write_json(parent_dir / "inbox" / f"msg-branch-{slug}-{run_ts()}.json",
                      {"text": text, "ts": now_iso(), "via": "conversation"})
    log.info("branch: %s handed back to %s (%d artefacts)", slug, parent_slug, copied)
    return {"parent": parent_slug, "copied": copied, "message": text}
