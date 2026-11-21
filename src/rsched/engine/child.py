"""The CHILD RUN — one concept, several scheduling modes (F338).

The single definition the child machinery reads from, so the prompt copy, observations and docs
cannot drift into separate mental models. A child run is an isolated run with its own
directory, budget and recipe, and a declared relationship to its parent. A parallel and a
sequential `child` (its `mode` field, D241) and a conversation `branch` differ only in MODE —
when it runs relative to the parent and who drives it.

Every mode obeys one contract:

1. **Isolation.** A child gets its OWN directory and never writes into its parent's.
2. **A budget of its own**, sliced from the parent's remainder as it starts. Parallel siblings
   are not netted against each other (by choice); the run-level budget still ends the tree once
   their usage folds back.
3. **A HAND-BACK.** Summary always; FILES by writing into its own `artifacts/`, which the engine
   copies to the parent's `artifacts/<handback_dirname(noun, key)>/`, naming the landed paths.
   Nothing in the action schema, so a non-child run pays nothing.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("rsched.engine.child")

# The scheduling modes: when a child runs relative to its parent, and who drives it.
PARALLEL = "parallel"        # the default: runs concurrently; the parent keeps working
SEQUENTIAL = "sequential"    # an ordered step: the parent waits for it before moving on
BRANCH = "branch"            # a conversation forked at a message; the USER drives it
# The escalation ladder: authority INVERTED — started by the ENGINE, its directive binds the run
# below it. Same three-part contract, so a mode rather than a new kind of child.
OVERSIGHT = "oversight"

# How each mode reads in prose the model sees — and, by its keys, the mode vocabulary itself.
MODE_NOUN = {
    PARALLEL: "parallel child run",
    SEQUENTIAL: "sequential child run",
    BRANCH: "branched child conversation",
    OVERSIGHT: "oversight run supervising the run below it",
}

# Where a child writes what it is handing back. Its OWN artifacts/ — not a special channel.
HANDBACK_SUBDIR = "artifacts"


#: The lower-case form the CLI transcript renders in its one-line rows (D241).
MODE_SHORT = {PARALLEL: "child", SEQUENTIAL: "child (seq)", BRANCH: "branch",
              OVERSIGHT: "oversight"}


def mode_noun(mode: str) -> str:
    """How this mode is named to the model. An unknown mode reads as a plain child run rather
    than leaking a raw enum value into the prompt.
    """
    return MODE_NOUN.get(mode, "child run")


def finished_headline(*, mode: str, n: int, label: str, status: str, turns: int,
                      workflow: str = "") -> str:
    """THE headline for a child's exit — one wording, whichever reporter gets there first.

    Two paths race to announce an exit (`control.announce_finished_subruns` and a `wait` in
    `obs_children`); this is the only place the sentence exists, the mode named inside it
    (D246). `workflow` is left out when unknown (a wait does not carry it).
    """
    what = f"pattern {workflow}, " if workflow else ""
    return (f"CHILD RUN FINISHED ({mode_noun(mode)}) — #{n} {label!r} "
            f"({what}status {status}, {turns} turns)")


def mode_short(mode: str) -> str:
    """The mode as one lower-case word, for the CLI transcript's one-line rows."""
    return MODE_SHORT.get(mode, "child")


#: The hand-back NOUNS — their own table, not MODE_NOUN keys: a detached background task is
#: not a mode (its budget never folds back) but shares the one hand-back implementation.
SUB = "sub"                  # a `child` in either mode — namespaced by the child's number
BACKGROUND = "bg"            # a detached task's delivery, namespaced by the task id
BRANCH_HANDBACK = "branch"   # a forked conversation's result, namespaced by the branch slug


def handback_dirname(noun: str, key: str | int) -> str:
    """The parent-relative directory one child's collected deliverables land in.

    Namespaced by the child's key so siblings cannot overwrite each other, and stable so a
    parent can name the path later.
    """
    return f"{HANDBACK_SUBDIR}/from-{noun}-{key}"


def collect_handback(src_dir: Path, parent_dir: Path, noun: str, key: str | int) -> tuple:
    """Copy a finished child's `artifacts/` into its parent's, and return the parent-relative
    paths that landed — the HAND-BACK half of the contract, for every mode.

    Overwriting on purpose (namespaced, idempotent on repeat hand-backs). Best-effort: a copy
    failure never turns a finished child into a failed one. Returns PATHS, not a count.

    The answer is enumerated from the SOURCE and confirmed against the destination: the slot
    is keyed by child NUMBER, so it accumulates other runs' files. The copy stays additive;
    only the answer is narrowed to this child.
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
    mine = sorted(p.relative_to(src_dir).as_posix()
                  for p in src_dir.rglob("*") if p.is_file())
    return tuple(f"{rel}/{p}" for p in mine if (dst / p).is_file())


def handback_paths_line(paths: tuple) -> str:
    """How a parent is told WHAT it was handed — "" when the child wrote nothing. One spelling
    for both racing reporters of a child's exit.
    """
    if not paths:
        return ""
    return ("Collected into your artifacts/: " + ", ".join(paths)
            + " — read them from there; the sender's own dir is not in your reach.")


def handback_text(*, headline: str, summary: str, paths: tuple, follow_on: str) -> str:
    """The one wording of a hand-back: what finished, what it said, what it left you, and what
    to do with it.

    `headline` and `follow_on` stay the caller's — the only parts that genuinely differ
    between modes.
    """
    body = [headline, "", summary.strip() or "(no summary was written.)"]
    if line := handback_paths_line(paths):
        body += ["", line]
    if follow_on:
        body += ["", follow_on]
    return "\n".join(body)
