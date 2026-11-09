"""WHO reads an answered decision, and WHEN — or that nothing ever will (2026-11-09).

An answer is filed into the asking dir's inbox and READ by a run: a live run at its next turn
boundary, otherwise the routine's next fire. The Decisions page said only "answered · queued",
so an answer whose reader never comes looked exactly like one whose reader is due tonight. Two
conversation answers sat seven weeks that way: each was filed during the conversation's LAST
turn, so the answer route saw a live run and did not resume it, and the run then finished
without draining it (the reap's sweep catches that case now — `daemon/runner_state.stranded_input`).

`annotate` stamps every answered, not-yet-consumed row with `pickup`:

- `live` — a run is active and drains the answer at its next turn boundary;
- `scheduled` + `at` — the routine's next fire: its own cron, its lane's, or an armed one-shot;
- `trigger` — no clock, but an event trigger the routine holds can start it;
- `never` + `why` — no run will read it — only when that is KNOWN: a routine whose clock the
  scheduler has not computed yet carries no `pickup` at all. The page lists these as
  UNDELIVERED, counts them as waiting on the operator, and offers `deliver` / `settle`
  (`web/api_question_deliver`).

The rows are COPIED: `open_decisions` is memoized, and a stamp written into the memo would
outlive the state it describes.
"""

from __future__ import annotations

from datetime import datetime

from .. import lanes, registry, schedule_once

_DONE = {"conversations": "the conversation has finished, and nothing resumes it",
         "background": "the background task has finished"}


def _one_shot(routines_home, slug: str) -> datetime | None:
    """The earliest armed one-shot for `slug` (docs/schedule-once.md), or None."""
    times = []
    for path in schedule_once.pending_requests(routines_home, slug):
        try:
            times.append(schedule_once.parse_fire_at(
                str(schedule_once.read_request(path).get("fire_at") or "")))
        except ValueError:
            continue
    return min(times, default=None)


def routine_pickup(info: registry.RoutineInfo | None, lane: dict | None, *,
                   own: datetime | None, lane_at: datetime | None,
                   one_shot: datetime | None) -> dict | None:
    """When a routine with no live run next reads its inbox. None when it cannot be told."""
    if info is None:
        return None
    if not info.fireable:
        why = "the routine reached its finish line" if info.retired else "the routine is off"
        return {"state": "never", "why": why}
    clock = lane_at if lane is not None else own
    if clock is None and (info.cfg.cron if lane is None else not lane.get("paused")):
        return None   # a clock exists the scheduler has not computed yet: unknown, never "never"
    at = min((t for t in (clock, one_shot) if t is not None), default=None)
    if at is not None:
        return {"state": "scheduled", "at": at.isoformat()}
    if info.cfg.triggers:
        return {"state": "trigger"}
    if lane is not None and lane.get("paused"):
        return {"state": "never", "why": f"its lane “{lane.get('name')}” is paused"}
    return {"state": "never", "why": "the routine has no schedule, lane or trigger"}


def annotate(state, items: list[dict]) -> list[dict]:
    """`items` with a `pickup` on every answered row a run has not consumed yet."""
    sched, runner = getattr(state, "scheduler", None), getattr(state, "runner", None)
    if sched is None or runner is None or not any(_in_flight(q) for q in items):
        return items
    server = state.server
    lane_of = lanes.scheduled_lane_by_member(server.routines_home)
    out = []
    for q in items:
        if not _in_flight(q):
            out.append(q)
            continue
        home = ("conversations" if q.get("conversation")
                else "background" if q.get("background") else "routines")
        slug = str(q.get("routine") or "")
        if runner.is_active(home, slug):
            pick: dict | None = {"state": "live"}
        elif home != "routines":
            pick = {"state": "never", "why": _DONE[home]}
        else:
            lane = lane_of.get(slug)
            pick = routine_pickup(
                registry.info(server, server.routines_home, slug), lane,
                own=sched.next_fires.get(slug),
                lane_at=sched.lane_next_fires.get(lane["id"]) if lane else None,
                one_shot=_one_shot(server.routines_home, slug))
        out.append({**q, "pickup": pick} if pick else q)
    return out


def _in_flight(q: dict) -> bool:
    return bool(q.get("answered")) and not q.get("settled")
