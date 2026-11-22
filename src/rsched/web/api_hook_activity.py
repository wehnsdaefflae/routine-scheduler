"""The hook stream's two readings, served to the console: what fires next, and who spoke into
which run (`readmodels/hook_activity`).

One route for both lists, because they are one panel and one fold of one file: asking for them
separately would parse `.control/hooks.jsonl` twice per refresh to answer halves of the same
question. Operator-only like the other fleet reads — the deliveries list carries the first line
of everything the operator typed into every run on the instance, which is exactly the
cross-routine reading `app.ROUTINE_TOKEN_DENIED_READS` exists to keep out of a run's reach.

Not on any bus-event path: the panel refreshes on its own timer and on `run_finished`, so this
answer is never recomputed per bus tick.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ..readmodels.hook_activity import (
    DEFAULT_HORIZON_H,
    DEFAULT_SINCE_H,
    MAX_HORIZON_H,
    MAX_SINCE_H,
    activity,
)

router = APIRouter(tags=["hooks"])


@router.get("/hook-activity")
def hook_activity(
        request: Request,
        horizon_h: Annotated[int, Query(ge=1, le=MAX_HORIZON_H)] = DEFAULT_HORIZON_H,
        since_h: Annotated[int, Query(ge=1, le=MAX_SINCE_H)] = DEFAULT_SINCE_H,
) -> dict:
    """`next_fires`: the one-shots still armed and firing within `horizon_h`, soonest first,
    each with its target routine, instant, reason and who armed it — and `overdue` true for one
    whose instant has passed and the daemon has not consumed yet (never dropped: that is a
    one-shot about to fire late, which is a thing to see).

    `deliveries`: injection and run-tree hooks from the last `since_h` hours, newest first —
    the run, its turn, WHO spoke (`who` renders the channel in words, `person` says whether it
    was a human at a console) and the freight's first line. A run-tree row whose write failed
    carries `undelivered` with the sentence that says so: the stream is the only record of that
    failure anywhere, because a run-tree hook gets no `R<n>`.

    Both windows are echoed back, so the panel renders the window it was actually served.
    """
    return activity(request.app.state.server, horizon_h=horizon_h, since_h=since_h)
