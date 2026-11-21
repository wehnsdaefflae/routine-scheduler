"""Retirement: what happens once a routine reaches its FINISH LINE (`finishline.py`).

Retirement is not a config write, so the engine still never writes `routine.yaml`:

- **Stopping firing is DERIVED.** The scheduler builds no fire entry for a routine whose finish
  line is reached (`registry.RoutineInfo.retired`); editing the finish line brings it back on
  the next rescan. `enabled` stays the operator's switch, written only by the web.
- **Making it permanent is a CLICK.** One `goal-reached` card on the Decisions page: approving
  writes `enabled: false`; declining reopens the outcomes. A line the CALENDAR reached cannot be
  declined — its date is changed instead, and that save withdraws the card. Doing nothing
  leaves it paused with the card standing.

Three parties complete a finish line: a RUN whose checked accounting proves its last outcome
(`maybe_propose_retirement`), the CALENDAR (`propose_if_due`, from the scheduler tick), and the
OPERATOR ticking an outcome only they judge (queued by the finish-line save).
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


def propose(server, slug: str, name: str, *, run_id: str = "") -> str:
    """Queue the one retirement card for a routine whose finish line is reached. Returns its id,
    or "" when it is not reached, is already queued, or is not a scheduled routine.

    Takes the server because `pending.queue` judges every record against the live vocabulary
    (D229).
    """
    routines_home = server.routines_home
    routine_dir = Path(routines_home) / slug
    doc = finishline.load(routine_dir)
    why = finishline.reached(doc)
    if not why or not (routine_dir / "routine.yaml").is_file() \
            or already_queued(routines_home, slug):
        return ""
    try:
        rec = pending.queue(
            server, kind=KIND, routine=slug, run_id=run_id,
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
    """Drop the retirement card of a routine whose finish line is no longer reached (its date
    moved or an outcome reopened). Returns the ids dropped.
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
    finish is what completed the finish line. Best-effort — never fails the run.
    """
    if ctx.depth > 0:
        return ""
    card = propose(ctx.server, ctx.routine.slug, ctx.routine.name, run_id=ctx.run_id)
    if card:
        ctx.transcript.event("stopping_update", {"goal_reached": True, "run_id": ctx.run_id,
                                                 "proposal": card})
    return card


def propose_if_due(server, slug: str, name: str) -> str:
    """From the scheduler's tick: a date outcome or the `until` date completes a finish line with
    no run at all, so the card has to come from the clock.
    """
    return propose(server, slug, name)
