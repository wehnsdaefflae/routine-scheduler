"""The durable record of an ARMED recipe-revision approval — so a leg that dies before it edits
anything does not take the operator's deliberate approval with it.

Approving a recipe-revision card (D200) resumes the proposing run as a narrow revise leg
(`engine/reviseleg.py`). The approval used to be CONSUMED at that moment, three times over: the
card was dropped from the Decisions queue, the one-shot unlock marker was cleared on read, and
the injected message was drained into the transcript. So a leg whose FIRST model call failed
through the whole fallback chain closed `failed` having taken no action, and the approved texts
— the operator's own wording, where he edited it on the card — existed nowhere any surface could
read them. An exhausted model chain is an ordinary transport fact, not a rare one, so this flow
has to survive one.

The shape is `daemon/reap_resume.py`'s, which answers the same question about a different leg:

* a DURABLE record FIRST — written before the resume is requested, so a leg that never takes a
  turn still leaves the approved lines on disk;
* a CAP (`MAX_ARMS`) read back off that record, so a card whose leg keeps dying cannot be
  re-armed without end;
* and the approval is RELEASED only by an AUTHORED finish of the leg — the one ending that says
  a MODEL, not the engine, decided the leg was done (`engine/loopend.finish_run`). Every
  engine-authored ending (an exhausted chain, a spent reserved turn, an abort) leaves the card
  standing, and the operator's click is still worth exactly what it was.

What this is NOT: a retry. Nothing here resumes anything and nothing here calls a model. It
keeps the card ACTIONABLE, which is the conservative half — re-driving a model call that just
failed through its entire chain is the daemon's business, not this flow's — and it keeps the
approved text on a surface the operator already reads instead of only in a transcript.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..ids import now_iso
from ..paths import atomic_write_json, read_json

log = logging.getLogger("rsched.engine.revisearm")

#: The arming record, beside the one-shot unlock marker in the proposing run's own dir — the run
#: whose leg was armed is the thing the record is about, and retention prunes the two together.
ARM_RECORD = "revise-arm.json"

#: How many legs one approved card may arm. The first failure is never the operator's fault — an
#: exhausted model chain is a transport fact — so a cap of one would throw his approval away for
#: something that had nothing to do with it. But a card whose leg dies every time has to stop
#: being offered rather than become a loop, so the count rides the record and is read back, the
#: way the restart resumes' cap is (`daemon/reap_resume.RESTART_RESUMES`).
MAX_ARMS = 3


def record_path(run_dir: Path) -> Path:
    return run_dir / ARM_RECORD


def armed(run_dir: Path) -> dict:
    """The arming record standing for this run's revise leg, or {} when there is none."""
    rec = read_json(record_path(run_dir))
    return rec if isinstance(rec, dict) else {}


def approved_lines(run_dir: Path) -> list[str]:
    """The wording the last arming actually told the leg to implement.

    Read back on a RE-ARM so the operator does not silently lose the edit he made on the card:
    his lines replaced the run's at the first click and are held nowhere else once the leg that
    was given them has died.
    """
    return [str(x) for x in (armed(run_dir).get("lines") or []) if str(x).strip()]


def arms_spent(run_dir: Path) -> int:
    return int(armed(run_dir).get("arms") or 0)


def arm_blocker(run_dir: Path) -> str:
    """Why this approval may NOT arm another leg, or "" when it may.

    Separated from the act for the reason `daemon/reap_resume.restart_resume_blocker` is: the
    sentence the card renders and the sentence the route refuses with are then the same one, so
    the operator never spends a click to discover a refusal.
    """
    spent = arms_spent(run_dir)
    if spent >= MAX_ARMS:
        return (f"this approval has already armed {spent} revise legs (cap {MAX_ARMS}) and none "
                "of them finished the edit — something is failing before the leg can work. Make "
                "the change from the routine's own recipe editor and discard this proposal")
    return ""


def arm(run_dir: Path, *, proposal: str, lines: list[str], edited: bool = False) -> int:
    """Record the approval DURABLY, before any resume is asked for; returns the arms now spent.

    `lines` is what the leg is actually told to implement — the operator's wording where he
    edited it on the card, the run's own where he did not — because that text is the thing a
    dead leg otherwise destroys. `edited` records WHOSE words those are, since a leg told the
    operator's sentence is its own would defend wording it never chose; the card itself keeps
    the run's lines, so nothing else remembers. `proposal` is the card's id, so the ending that
    releases the approval knows which row to consume.
    """
    spent = arms_spent(run_dir) + 1
    atomic_write_json(record_path(run_dir),
                      {"proposal": proposal, "lines": list(lines), "arms": spent,
                       "edited": bool(edited), "armed_at": now_iso()})
    return spent


def disarm(run_dir: Path) -> None:
    """Give back the attempt an arming that never happened just spent.

    A refused resume leaves nothing unlocked (the route clears the marker), so it must leave
    nothing CHARGED either: a cap that counted legs which never started would retire a card
    after three refusals the operator got no leg out of at all.
    """
    rec = armed(run_dir)
    if not rec:
        return
    spent = arms_spent(run_dir) - 1
    try:
        if spent <= 0:
            record_path(run_dir).unlink(missing_ok=True)
        else:
            atomic_write_json(record_path(run_dir), {**rec, "arms": spent})
    except OSError:
        log.warning("could not give back a refused revise arming in %s", run_dir)


def _standing_note(rec: dict, left: int) -> str:
    """What the leg's own result says about an approval it did not use up."""
    pid = str(rec.get("proposal") or "the proposal")
    if left <= 0:
        return (f"\n[The approved recipe revision was NOT applied: this leg ended without an "
                f"authored finish, and {pid} has now spent its {MAX_ARMS} arming attempts. Its "
                "approved lines are on the record in this run dir; make the change from the "
                "routine's own recipe editor.]")
    return (f"\n[The approved recipe revision was NOT applied: this leg ended without an "
            f"authored finish, so the approval was not consumed. {pid} still stands on the "
            f"Decisions page with the approved lines and can arm another leg "
            f"({left} of {MAX_ARMS} attempts left).]")


def settle(ctx, *, authored: bool) -> str:
    """End a narrow revise leg's hold on the approval; returns the line for the leg's result.

    An AUTHORED finish consumes the card: the leg read the lines and said what it did with them,
    which is the whole of what the operator approved — including a leg that decided on reading
    that a line was wrong and left the file alone, since that judgement is the run's to make.
    Any other ending keeps the card, and says so where the operator reads the run.

    Best-effort, like `recipeproposal.maybe_propose`: a store write never fails a finished run.
    """
    from .. import pending

    rec = armed(ctx.run_dir)
    if not rec:
        return ""
    try:
        if authored:
            pending.drop(ctx.server.routines_home, str(rec.get("proposal") or ""))
            record_path(ctx.run_dir).unlink(missing_ok=True)
            return ""
        return _standing_note(rec, MAX_ARMS - arms_spent(ctx.run_dir))
    except OSError:
        log.warning("could not settle the revise arming for %s", ctx.run_id)
        return ""
