"""The CHILD RUN — one concept, three scheduling modes.

This module is the single definition the rest of the child machinery reads from. It exists
because the system had grown three names for one thing (F338): a `spawn` produced a
"subroutine", a `subtask` produced a "subtask", and conversation branching (F325) was about to
add a third word for the same shape. Three names invited three mental models, and the prompt
copy drifted between them until it claimed something false — that children share the parent's
working directory (R409/R410), a lie that cost a run a recovery detour.

**A child run is:** an isolated run with its own directory, its own budget, its own recipe or
pattern, and a declared relationship to its parent. That is the whole concept. What varies
between `spawn`, `subtask` and `branch` is only the MODE — *when* it runs relative to the
parent and *who* drives it — never what a child IS.

Deliberately NOT a fourth action kind: `spawn`, `subtask` and (F325) the conversation fork keep
their own names at the action surface, because each names a different scheduling intent a run
actually chooses between. They share this contract underneath.

The contract has three parts, and every mode obeys all three:

1. **Isolation.** A child gets its OWN directory and never writes into its parent's. Concurrent
   siblings therefore cannot race a shared tree, and the engine arbitrates nothing.
2. **A budget of its own**, sliced from the parent's remainder — a child can never outspend the
   run that started it.
3. **A declared HAND-BACK.** A child returns its summary always, and returns FILES by writing
   them into its own `artifacts/` — the same convention the Artifacts panel and detached
   background tasks already use. The engine copies those into the parent's
   `artifacts/<handback_dirname(noun, key)>/` and NAMES the landed paths in the notification.
   Nothing is declared in the action schema, so a non-child run pays nothing for this and a
   child that writes nothing hands back only its summary. Before this the parent had to know
   the child's dir and search it — a procedure every routine reinvented.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("rsched.engine.child")

# The scheduling modes. A mode answers "when does this child run, relative to its parent, and
# who drives it" — nothing else. Adding a mode is adding a schedule, never a new kind of child.
PARALLEL = "parallel"        # `spawn` — runs concurrently; the parent keeps working
SEQUENTIAL = "sequential"    # `subtask` — the parent waits for this one before moving on
BRANCH = "branch"            # F325 — a conversation forked at a message; the USER drives it

# How each mode reads in prose the model sees — and, by its keys, the mode vocabulary itself.
# Kept here rather than inline at each call site so the three surfaces (kind copy, observations,
# docs) cannot drift apart again, which is the failure F338 is about.
MODE_NOUN = {
    PARALLEL: "parallel child run",
    SEQUENTIAL: "sequential child run",
    BRANCH: "branched child conversation",
}

# Where a child writes what it is handing back. Its OWN artifacts/ — not a special channel.
HANDBACK_SUBDIR = "artifacts"


#: The SHOUTED form, for an observation that announces a child's exit — and the lower-case
#: form the CLI transcript renders. Both were spelled inline at their call sites as
#: `"SUBTASK" if mode == "sequential" else "SUB-WORKFLOW"`, which is the drift this module
#: exists to prevent: a fourth mode would have read as a sub-workflow in one place, a subrun in
#: another, and by its real name only here.
MODE_SHOUT = {PARALLEL: "SUB-WORKFLOW", SEQUENTIAL: "SUBTASK", BRANCH: "BRANCH"}
MODE_SHORT = {PARALLEL: "subrun", SEQUENTIAL: "subtask", BRANCH: "branch"}


def mode_noun(mode: str) -> str:
    """How this mode is named to the model. An unknown mode reads as a plain child run rather
    than leaking a raw enum value into the prompt.
    """
    return MODE_NOUN.get(mode, "child run")


def mode_shout(mode: str) -> str:
    """The mode as an observation headline names it (`SUBTASK 2 'x' FINISHED`)."""
    return MODE_SHOUT.get(mode, "CHILD RUN")


def mode_short(mode: str) -> str:
    """The mode as one lower-case word, for the CLI transcript's one-line rows."""
    return MODE_SHORT.get(mode, "child")


#: The hand-back NOUNS, and deliberately their own table rather than keys of MODE_NOUN: the
#: mode dicts above ARE the mode vocabulary, and a detached background task is not a mode (its
#: budget never folds back into a parent — docs/child-runs.md § Process model). What it does
#: share is this: it writes into its own `artifacts/`, the engine copies them up, and the
#: parent is told the landed paths. That is the hand-back, and it has one implementation.
SUB = "sub"                  # spawn / subtask — namespaced by the child's number
BACKGROUND = "bg"            # a detached task's delivery, namespaced by the task id
BRANCH_HANDBACK = "branch"   # a forked conversation's result, namespaced by the branch slug


def handback_dirname(noun: str, key: str | int) -> str:
    """The parent-relative directory one child's collected deliverables land in.

    Namespaced by the child's own key so concurrent siblings cannot overwrite each other, and
    stable so a parent can name the path in its own later work. The three spellings used to
    live at their three call sites (`from-sub-`, `from-bg-`, `from-branch-`), which is how a
    module claiming to own the hand-back came to know only one of them.
    """
    return f"{HANDBACK_SUBDIR}/from-{noun}-{key}"


def collect_handback(src_dir: Path, parent_dir: Path, noun: str, key: str | int) -> tuple:
    """Copy a finished child's `artifacts/` into its parent's, and return the parent-relative
    paths that landed — the HAND-BACK half of the contract, for every mode.

    Overwriting on purpose: namespaced, so it never clobbers the parent's own files, and
    idempotent when a child hands back more than once as it goes. Best-effort — a copy failure
    must never turn a finished child into a failed one; the summary still arrives.

    Returns PATHS, not a count, because that is the difference between a parent that can read
    what it was given and one that has to go and list a directory to find out.
    """
    if not src_dir.is_dir() or not any(src_dir.iterdir()):
        return ()
    rel = handback_dirname(noun, key)
    dst = parent_dir / rel
    try:
        shutil.copytree(src_dir, dst, dirs_exist_ok=True)
    except OSError as exc:
        log.warning("hand-back %s: could not collect artifacts: %s", rel, exc)
        return ()
    return tuple(sorted(f"{rel}/{p.relative_to(dst).as_posix()}"
                        for p in dst.rglob("*") if p.is_file()))


def handback_paths_line(paths: tuple) -> str:
    """How a parent is told WHAT it was handed — "" when the child wrote nothing.

    One spelling, because the two reporters of a child's exit (the `wait` observation and the
    turn-boundary announcement) race each other and whichever wins is what the parent reads.
    """
    if not paths:
        return ""
    return ("Collected into your artifacts/: " + ", ".join(paths)
            + " — read them from there; the sender's own dir is not in your reach.")


def handback_text(*, headline: str, summary: str, paths: tuple, follow_on: str) -> str:
    """The one wording of a hand-back: what finished, what it said, what it left you, and what
    to do with it.

    Three renderers used to spell this, and they had already drifted: a subtask's parent was
    told the PATHS, while a branch's and a background task's were told a COUNT and had to list
    a directory to learn the names. `headline` and `follow_on` stay the caller's — a sequential
    child's result feeds the next one, a background task's is relayed to the user, a branch's
    is weighed against the parent's own line of work — and only those genuinely differ.
    """
    body = [headline, "", summary.strip() or "(no summary was written.)"]
    if line := handback_paths_line(paths):
        body += ["", line]
    if follow_on:
        body += ["", follow_on]
    return "\n".join(body)
