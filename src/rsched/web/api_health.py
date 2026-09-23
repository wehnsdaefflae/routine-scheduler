"""The fleet's blocked-work endpoint: the health stream's six "this did not happen" events,
served to the console.

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

from ..readmodels.health_stream import DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS, blocked_fleet

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
