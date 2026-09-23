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
a summary plus its artefacts into the parent as a message and files, through the same
`engine/child.py` hand-back a subtask and a detached background task use. The parent then
chooses what to do with them.

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

from .engine import child, inbox
from .engine.history import cut_index_for_turn
from .engine.transcript import read_events
from .ids import now_iso, run_ts
from .paths import atomic_write, atomic_write_json, atomic_write_yaml, read_yaml

if TYPE_CHECKING:
    from .config import ServerConfig

log = logging.getLogger("rsched.branches")

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
    # The branch is a NEW routine and must load under its OWN slug (its dir name). The parent's
    # routine.yaml we just copied carries the PARENT slug; leaving it makes the branch load AS the
    # parent (load_routine reads raw["slug"] first, only then the dir name, and flags the mismatch)
    # — a collision in the runner's slug-keyed active map that wedges BOTH conversations: the parent
    # becomes unreachable and a message to the branch is refused as an overrun of the parent's slug.
    raw["slug"] = slug
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


def hand_back(server: ServerConfig, *, branch_dir: Path, slug: str, summary: str) -> dict:
    """Deliver a branch's result to its parent: artefacts copied into the parent's
    `artifacts/from-branch-<slug>/`, and one inbox message carrying the summary and naming
    them. Returns `{parent, copied, message}`.

    The copy, the directory name and the wording are `engine/child.py`'s, the same three a
    subtask's and a detached task's hand-back use (`daemon/detached_delivery.deliver_one`) —
    that is the point: a hand-back is the child-run result, and the parent already knows how to
    read one.

    Delivery does NOT wake the parent; its next reply drains the message, the way every other
    inbox message reaches a conversation. `via="branch"` is what holds that promise: it is a
    LIVE via (the parent's next reply consumes it) but not a USER one, so the reap never
    resumes the parent for it, the composer never offers it as the user's own editable text,
    and it does not count as the user having spoken (inbox.MACHINE_VIAS). Raises ValueError
    when the conversation is not a branch or its parent is gone.
    """
    raw = read_yaml(branch_dir / "routine.yaml", {})
    parent = raw.get("parent") or {}
    parent_slug = str(parent.get("slug") or "")
    if not parent_slug:
        raise ValueError("this conversation is not a branch — it has no parent to hand back to")
    parent_dir = server.conversations_home / parent_slug
    if not (parent_dir / "routine.yaml").is_file():
        raise ValueError(f"the parent conversation {parent_slug!r} no longer exists")

    paths = child.collect_handback(branch_dir / "artifacts", parent_dir,
                                   child.BRANCH_HANDBACK, slug)
    name = str(raw.get("name") or slug)
    text = child.handback_text(
        headline=f"[branch handed back] The branch “{name}” ({slug}) handed its result "
                 "back to you.",
        summary=summary, paths=paths,
        follow_on="This is a hand-back, not a merge: the branch's conversation stays its own. "
                  "Take what is useful from the summary and the files, and tell me what you "
                  "make of it.")
    inbox.file_message(parent_dir, text, via="branch", name=f"branch-{slug}-{run_ts()}")
    log.info("branch: %s handed back to %s (%d artefacts)", slug, parent_slug, len(paths))
    return {"parent": parent_slug, "copied": len(paths), "message": text}
