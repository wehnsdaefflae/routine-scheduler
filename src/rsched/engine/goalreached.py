"""Retirement: what happens the run a routine's FINAL GOAL is met.

The operator's ask was plain — "the ability to disable themselves once they think they reached
it" — and it runs straight into the invariant that a run never writes `routine.yaml` and the
engine never writes config. Both hold here, because retirement is not a config write at all:

- **Stopping firing is DERIVED.** `stopping.goal_reached()` reads the routine's own goal document,
  and the scheduler declines to build a fire table entry for a routine whose goal is satisfied
  (`registry.RoutineInfo.retired`). Nothing is written, nothing is toggled, and clearing a goal
  condition in the panel brings the routine back on its next rescan. `enabled` stays exactly what
  it was: the user's switch, written only by the web.
- **Making it permanent is a CLICK.** This module queues one `goal-reached` proposal on the
  Decisions page through the existing bridge (`pending.py`) — the same queue `create_routine` and
  `manage_group` use when a scheduled run has no user in the loop. Approving it writes
  `enabled: false` and drops the routine from its group chains; declining it reopens the goal, so
  the routine resumes on its next tick. Doing nothing leaves it paused with the proposal standing,
  which is the honest state: the routine says it is finished and nobody has confirmed.

Two properties make this safe enough to act on without a human first:

1. **Only the USER can create a goal condition.** `api_stopping` is the sole writer of the goal
   document, so a run cannot invent its own finish line — only report against one already drawn.
2. **The claim is checked.** A `met` verdict goes through the finish gate's verifier subcall
   against the run's own transcript before `record_accounting` stamps it (fail-open, at most one
   challenge per condition — see `engine/verifier.py`).

There is no new action kind and no new field on `finish`. The run already says the goal is reached
the same way it says anything else about its conditions: `[s<n>] met — <evidence>`.
"""

from __future__ import annotations

import logging

from .. import pending
from . import stopping

log = logging.getLogger("rsched.goalreached")

KIND = "goal-reached"


def already_queued(routines_home, slug: str) -> bool:
    """Is a retirement proposal for this routine already on the Decisions page? Queue-once, the
    way `daemon/library_watch` dedupes its own drift record: a met goal is STICKY, so without this
    every later run would file an identical proposal.
    """
    return any(r.get("kind") == KIND and r.get("routine") == slug
               for r in pending.load_all(routines_home))


def maybe_propose_retirement(ctx) -> str:
    """Called from the finish gate once the accounting is recorded. Queues a retirement proposal
    when this run's finish is what completed the goal. Returns the proposal id, or "".

    Best-effort by construction: a run that reached its routine's whole goal must not be turned
    into a failed one because a proposal could not be written.
    """
    if ctx.depth > 0:
        return ""     # a child has no schedule of its own to retire
    doc = stopping.load(ctx.routine.dir)
    verdict = stopping.evaluate(doc)
    if verdict["goal_satisfied"] is not True:
        return ""
    home = ctx.server.routines_home
    if not (home / ctx.routine.slug / "routine.yaml").is_file():
        return ""     # a conversation: it has no `enabled` and no schedule to stop
    if already_queued(home, ctx.routine.slug):
        return ""
    met = [c for c in doc["conditions"] if c["scope"] == "goal" and c["status"] == "met"]
    try:
        rec = pending.queue(
            home, kind=KIND, routine=ctx.routine.slug, run_id=ctx.run_id,
            fields={"conditions": [{"id": c["id"], "text": c["text"], "note": c["note"],
                                    "resolved_run": c["resolved_run"],
                                    "disputed": c["disputed"]} for c in met],
                    "groups": verdict["groups"]},
            summary=f"{ctx.routine.name or ctx.routine.slug} reports its final goal met — "
                    f"{len(met)} condition(s). It has stopped running; retire it or reopen "
                    f"the goal.")
    except OSError as exc:
        log.warning("goal-reached: could not queue a retirement proposal for %s: %s",
                    ctx.routine.slug, exc)
        return ""
    log.warning("goal-reached: %s met its final goal in %s — scheduling stopped, proposal %s",
                ctx.routine.slug, ctx.run_id, rec["id"])
    ctx.transcript.event("stopping_update", {"goal_reached": True, "run_id": ctx.run_id,
                                             "proposal": rec["id"],
                                             "conditions": [c["id"] for c in met]})
    return str(rec["id"])
