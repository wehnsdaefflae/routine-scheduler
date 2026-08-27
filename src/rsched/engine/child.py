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
   `artifacts/<handback_dirname(n)>/` and NAMES the landed paths in the finished notification.
   Nothing is declared in the action schema, so a non-child run pays nothing for this and a
   child that writes nothing hands back only its summary. Before this the parent had to know
   the child's dir and search it — a procedure every routine reinvented.
"""

from __future__ import annotations

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


def mode_noun(mode: str) -> str:
    """How this mode is named to the model. An unknown mode reads as a plain child run rather
    than leaking a raw enum value into the prompt.
    """
    return MODE_NOUN.get(mode, "child run")


def handback_dirname(n: int) -> str:
    """The parent-relative directory one child's collected deliverables land in.

    Namespaced by the child's number so concurrent siblings cannot overwrite each other, and
    stable so a parent can name the path in its own later work.
    """
    return f"{HANDBACK_SUBDIR}/from-sub-{n}"
