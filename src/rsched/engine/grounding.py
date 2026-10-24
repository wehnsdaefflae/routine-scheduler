"""What GROUNDS a path for the destruction gate, and the sentences that say so (R2879).

`write_file` refuses to overwrite a file outside the routine's own dir that this run has never
read, and `fsops` guards delete and move-src the same way: the model must have LOOKED at what it
destroys (`RunContext.seen_paths`). Four messages across `fileops.py` and `fsops.py` depend on
one fact — WHICH looks count — so the fact and its wording live here, once, instead of being
spelled out in each and drifting apart.

**A refused read is a look.** `read_file` on a binary or oversized file adds the path to
`seen_paths` on the refusal itself ("a stat IS a look", 0.339.0): the size is the whole of what
read_file can tell about such a file, so demanding a successful read would demand the impossible
— and did, once: a run told to "read_file it first" about a season pack read a 1.5 GB .mkv to
comply and swap-thrashed the host (2026-09-14).

**And it must SAY so.** Until R2879 the refusal performed that side effect silently, so a run
that did the right sequence — read, refused, move — could not tell from the words that it had
gained anything: it read the refusal as a dead end, spent a turn being confused, and reported
the gate as a defect. A refusal that performs the side effect its caller needs and does not
mention it is indistinguishable from one that did nothing. That is the same class as a server
log nobody reads (F754) — the fix is a clause, never a behaviour change.
"""

from __future__ import annotations

#: Tail of a read refusal that nevertheless grounded the path: what the caller still earned.
REFUSAL_GROUNDS = ("; the size is all read_file can tell you about it, and this look is "
                   "recorded — a move, delete or overwrite of this path is now allowed")

#: What a destruction refusal says about reaching the state it demands — a refused read counts,
#: so the run never has to make the read succeed to get past the gate.
WHAT_COUNTS = ("read_file it first (a directory reads as its listing; for a binary or oversized "
               "file the REFUSAL itself counts, since its size is all read_file can tell you)")

#: The whole overwrite refusal: write_file replaces a file wholesale, so the gate is the same.
OVERWRITE_REFUSAL = ("this OVERWRITES an existing file this run has never read — " + WHAT_COUNTS
                     + ", then overwrite knowingly; or use edit_file with a verbatim anchor "
                     "for a targeted change")


def destruction_refusal(what: str) -> str:
    """Why a delete or a move of an unseen path outside the own dir is refused. `what` is the
    verb as the action's observation names it ("deletes", "moves").
    """
    return (f"this {what} a path outside the routine's own dir that this run has never read — "
            + WHAT_COUNTS + ", then remove it knowingly, so a stray call cannot destroy "
            "something sight-unseen")
