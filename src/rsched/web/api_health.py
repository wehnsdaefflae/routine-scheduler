"""The fleet's blocked-work endpoint: the health stream's "this did not happen" events
(`health_stream.BLOCKED_EVENTS`), served to the console.

Separate from `/api/stats` on purpose. Stats answers "what did the fleet spend"; this answers
"what did the fleet owe and not deliver", and those have different sources (the health stream,
not the usage stream), different windows and different readers. A refused fire, a stopped
chain and a capped trigger produce NO run, so nothing on the Runs, Items or Stats surfaces can
carry them — before this endpoint the only reader was a nightly recipe reading the file over
ssh (readmodels/health_stream).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ..readmodels.health_stream import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_DAYS,
    MAX_LIMIT,
    MAX_WINDOW_DAYS,
    blocked_fleet,
    recent_events,
)

router = APIRouter(tags=["health"])


@router.get("/health/blocked")
def blocked(request: Request,
            days: Annotated[int, Query(ge=1, le=MAX_WINDOW_DAYS)] = DEFAULT_WINDOW_DAYS,
            ) -> dict:
    """Work the fleet owed and did not do in the last `days`: `rows` is one entry per
    (event, subject) with a count, first/last stamps and the newest detail, newest first;
    `vocabulary` is what each event name means, so the console renders a label it was not
    compiled with. `total` is 0 on a healthy instance — an empty list is the good reading.
    """
    return blocked_fleet(request.app.state.server, days=days)


@router.get("/health/events")
def events(request: Request,
           limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
           routine: str = "",
           event: str = "",
           ) -> dict:
    """The health stream's own RECORDS, newest first — `events`, with `total` (how many
    matched) and `limit` (how many of them this answer carries).

    The stream is the instance's record of what fired, what was gated and what was refused, and
    from inside a routine run it is unreadable: `.control/` is not listable for uid 1000 on this
    instance, so `gu rsched logs` raises `PermissionError [Errno 13]` even though the file it
    wants is 0644 (R2764). This web process already parses that file for `/health/blocked`, so
    the route exists to make the stream reachable at all — a run fetches it like any other
    read surface.

    `/health/blocked` stays the right read for "what did the fleet owe and not deliver": it
    folds and it windows. This one neither folds nor windows — `routine` and `event` narrow it
    by exact name, a bounded `limit` takes the newest, and every event the writers produce is
    visible, including the ones no fold keeps.
    """
    return recent_events(request.app.state.server, limit=limit, routine=routine, event=event)
