"""Retirement: what happens once a routine reaches its FINISH LINE (`finishline.py`).

The operator's ask was plain — "the ability to disable themselves once they think they reached
it" — and it runs straight into the invariant that a run never writes `routine.yaml` and the
engine never writes config. Both hold here, because retirement is not a config write at all:

- **Stopping firing is DERIVED.** `finishline.goal_reached()` reads the routine's own finish
  line; the scheduler declines to build a fire-table entry for a routine that reached it
  (`registry.RoutineInfo.retired`). Nothing is written, nothing is toggled, and editing the
  finish line on the routine page brings the routine back on its next rescan. `enabled` stays
  exactly what it was: the operator's switch, written only by the web.
- **Making it permanent is a CLICK.** This module queues one `goal-reached` card on the Decisions
  page (`pending.py`). Approving writes `enabled: false`, which lane chains then skip past;
  declining reopens the outcomes, so the routine resumes on its next tick. A finish line the
  CALENDAR reached cannot be declined — reopening changes nothing a date decides — so its date
  is changed on the routine page instead; that save withdraws the card. Doing nothing leaves
  it paused with the card standing — the honest state: it says it is finished and nobody has
  confirmed.

Three parties can complete a finish line, each reaching this module its own way: a RUN whose
accounting proves its last open outcome (`maybe_propose_retirement`, from the finish gate, after
the claim was checked against its transcript), the CALENDAR for a date outcome or the `until`
date (`propose_if_due`, from the scheduler's tick — no run is involved), and the OPERATOR ticking
an outcome only they judge (the same card, queued by the finish-line save).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import pending
from . import finishline

log = logging.getLogger("rsched.goalreached")

KIND = "goal-reached"


def already_queued(routines_home, slug: str) -> bool:
    """Is a retirement card for this routine already on the Decisions page? Queue-once: a
    reached finish line stays reached, so without this every tick would file another.
    """
    return any(r.get("kind") == KIND and r.get("routine") == slug
               for r in pending.load_all(routines_home))


def propose(routines_home: Path, slug: str, name: str, *, run_id: str = "") -> str:
    """Queue the one retirement card for a routine whose finish line is reached. Returns its id,
    or "" when it is not reached, is already queued, or is not a scheduled routine.
    """
    routine_dir = Path(routines_home) / slug
    doc = finishline.load(routine_dir)
    why = finishline.reached(doc)
    if not why or not (routine_dir / "routine.yaml").is_file() \
            or already_queued(routines_home, slug):
        return ""
    try:
        rec = pending.queue(
            routines_home, kind=KIND, routine=slug, run_id=run_id,
            fields={"outcomes": [{k: o[k] for k in ("id", "text", "judge", "date", "status",
                                                    "evidence", "met_run", "disputed")}
                                 for o in doc["outcomes"]],
                    "until": doc["until"], "why": why},
            summary=f"{name or slug} reached its finish line — {why}. It has stopped running; "
                    "retire it, or reopen the finish line to keep it going.")
    except OSError as exc:
        log.warning("goal-reached: could not queue a retirement card for %s: %s", slug, exc)
        return ""
    log.warning("goal-reached: %s reached its finish line (%s) — scheduling stopped, card %s",
                slug, why, rec["id"])
    return str(rec["id"])


def withdraw(routines_home: Path, slug: str) -> list[str]:
    """Drop the retirement card of a routine whose finish line is no longer reached — the
    operator moved its date or reopened an outcome on the routine page; a card proposing to
    retire a routine that runs again would be a lie. Returns the ids dropped.
    """
    if finishline.goal_reached(Path(routines_home) / slug):
        return []
    dropped = [str(r["id"]) for r in pending.load_all(routines_home)
               if r.get("kind") == KIND and r.get("routine") == slug]
    for pid in dropped:
        pending.drop(routines_home, pid)
    return dropped


def maybe_propose_retirement(ctx) -> str:
    """From the finish gate, once the accounting is recorded: queue the card when this run's
    finish is what completed the finish line. Best-effort — a run that reached its routine's
    whole goal must not be turned into a failed one because a card could not be written.
    """
    if ctx.depth > 0:
        return ""
    card = propose(ctx.server.routines_home, ctx.routine.slug, ctx.routine.name,
                   run_id=ctx.run_id)
    if card:
        ctx.transcript.event("stopping_update", {"goal_reached": True, "run_id": ctx.run_id,
                                                 "proposal": card})
    return card


def propose_if_due(routines_home: Path, slug: str, name: str) -> str:
    """From the scheduler's tick: a date outcome or the `until` date completes a finish line with
    no run at all, so the card has to come from the clock.
    """
    return propose(routines_home, slug, name)
