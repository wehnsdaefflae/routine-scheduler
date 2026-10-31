"""RECIPE REVISION INVITATIONS — a revision one routine proposed for another, waiting for the
routine that owns the recipe to run (D198).

The vocabulary half of a `rsched.spool` rider (`spool.py`: the family name, the prefix and what
a record means — everything mechanical is there). An entry is one revision addressed to the
TARGET routine: the prose, who proposed it, and the run that did.

WHY A SPOOL AND NOT A WRITE. The proposer never edits the target's files. `web/api_recipe_
revision.py` already established the shape for the one existing recipe-revision card: a
revision is PROSE, not a diff — "the scan stage should read the cursor before the feed" — and
turning prose into an edit needs the context of the run that owns the recipe. So an approved
revision is HANDED to the target: its next run boots with the recipe unlocked for that leg
(`engine/revise.py`, the same one-shot marker the run view's checkbox and D200's card use),
reads the invitation as its instruction, makes the edit, and the engine commits it. One unlock
path, not a second that can drift from it.

WHY THE TARGET'S NEXT RUN, and not a resume of the proposer's leg (which is what D200's card
does): the proposer's transcript holds the REASONING for the change, but the recipe belongs to
the target, and a routine's recipe must be edited by a run that can read what that recipe is
for. The target's next ordinary fire is the first moment such a run exists.

MID-RUN SAFETY, which the operator's sentence does not settle and this store therefore does not
decide: an invitation is consumed at a run's INIT (`engine/loopsetup`) and never mid-flight, so
a run already executing its recipe is never rewritten underneath itself. A revision filed while
the target is running simply waits for the fire after it. That is the conservative reading; the
hand-back names it as the operator's open decision.
"""

from __future__ import annotations

from pathlib import Path

from . import spool
from .ids import now_iso

#: The spool family under `<routines_home>/.control/`, and the entry prefix (queue-ordered:
#: two routines may each propose a revision before the target next runs, and both are kept).
FAMILY = "recipe-revise"
PREFIX = "rr"
#: Past this many waiting invitations the proposer is refused rather than the queue growing
#: without bound — `triggers.MAX_PENDING_EVENTS`' reason, at the size a recipe change warrants.
MAX_PENDING = 8


def invite(routines_home: Path, target: str, *, lines: list[str], by: str, run_id: str,
           approved_by: str = "") -> Path | None:
    """File one invitation for `target`, or None when its queue is full.

    `approved_by` records WHAT authorized it — a decision id when the operator clicked, or the
    dial level that armed it without a card. Kept on the record because "who may rewrite whose
    recipe" is the question this path answers, and the answer has to be auditable per entry.
    """
    if len(pending(routines_home, target)) >= MAX_PENDING:
        return None
    return spool.write(routines_home, FAMILY, target,
                       {"lines": list(lines), "by": by, "run_id": run_id,
                        "approved_by": approved_by, "filed_at": now_iso()},
                       prefix=PREFIX)


def pending(routines_home: Path, target: str) -> list[Path]:
    """`target`'s unconsumed invitations, in queue order."""
    return spool.pending(routines_home, FAMILY, target, PREFIX)


def slugs_with_invitations(routines_home: Path) -> list[str]:
    """Every routine with a revision waiting — what the routine page and a sweep read."""
    return spool.slugs_with(routines_home, FAMILY, PREFIX)


def take(routines_home: Path, target: str) -> list[dict]:
    """Consume every waiting invitation for `target` and return the records.

    One-shot, like `engine/revise.clear_revise_marker`: the entry is unlinked as it is read, so
    a leg that is handed the prose cannot be handed it again by the next fire. Read before
    unlink, and an unreadable entry is dropped rather than retried forever — an entry that
    cannot be parsed is not an instruction anybody can act on.
    """
    from .paths import read_json

    out = []
    for path in pending(routines_home, target):
        rec = read_json(path)
        if isinstance(rec, dict) and [str(x) for x in (rec.get("lines") or []) if str(x).strip()]:
            out.append(rec)
        path.unlink(missing_ok=True)
    return out


def instruction(records: list[dict]) -> str:
    """What the target's leg is TOLD — the framing `web/api_recipe_revision._instruction`
    established, with the one thing it never has to say: these words are another routine's.

    A leg told its own proposal is handed back needs the authorization, not an explanation. A
    leg handed SOMEONE ELSE's reading of its recipe needs the opposite: whose words these are,
    what authorized them, and that it may refuse a line it finds wrong on reading — because it
    is the run that can see what the recipe is for, and an edit it cannot justify is worse than
    the drift it was asked to fix.
    """
    body = "\n".join(f"- {line}" for rec in records
                     for line in (str(x).strip() for x in (rec.get("lines") or []))
                     if line)
    who = ", ".join(dict.fromkeys(str(rec.get("by") or "another routine") for rec in records))
    return (f"RECIPE REVISION handed to you by {who}, authorized by the operator (D198). THE "
            "LINES BELOW ARE NOT YOURS — they are what another routine concluded about your "
            "recipe from watching your runs. Your own recipe files (main.md / stages/ / "
            "tuning.yaml) are writable to you on THIS leg only.\n\n" + body + "\n\nMake the "
            "edits with edit_file (a narrow anchor, not a rewrite), read back what you wrote, "
            "note the change in your LEDGER, and say in your summary what you changed and on "
            "whose line. You own this recipe: if a line is wrong about what your runs actually "
            "do, do NOT make that edit — say so in the summary with what you saw instead, and "
            "leave the file alone. `routine.yaml` is the operator's and stays sealed; a line "
            "that needs a config change goes out as a deferred ask_user carrying the patch.")
