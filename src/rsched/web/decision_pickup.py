"""The Decisions rows' READER stamp — `pickup` on every answer a run has not consumed yet.

What the states mean, and why `never` is stated only when known, is `readmodels/answer_pickup`
(the one derivation; the daily owed-work sweep reads it too). This stamps it on the rows
`open_decisions` returns. The rows are COPIED: `open_decisions` is memoized, and a stamp written
into the memo would outlive the state it describes.
"""

from __future__ import annotations

from .. import lanes
from ..readmodels.answer_pickup import pickup_of


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
        pick = pickup_of(server, home, str(q.get("routine") or ""),
                         next_fires=sched.next_fires, lane_next_fires=sched.lane_next_fires,
                         is_active=runner.is_active, lane_of=lane_of)
        out.append({**q, "pickup": pick} if pick else q)
    return out


def _in_flight(q: dict) -> bool:
    return bool(q.get("answered")) and not q.get("settled")
