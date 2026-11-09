"""Two reads of the health stream that ANSWER ONE QUESTION each, rather than fold the fleet.

`readmodels/health_stream` owns the stream's one parser and the blocked-fleet fold the console's
blocked-work surface reads. These two read the same parsed records for one caller each: the
newest records by name (`/api/health/events`, R2764) and one routine's partial-finish split
(its health card). Split out 2026-11-09 when `health_stream.py` reached the size bar and the
blocked vocabulary inside it could not take another event.
"""

from __future__ import annotations

from .. import runcorrection
from ..config import ServerConfig
from .health_stream import (
    DEFAULT_WINDOW_DAYS,
    ENDING_EVENTS,
    MAX_WINDOW_DAYS,
    _corrected_after,
    _corrections,
    _since,
    _window,
    health_records,
)

#: How many RECORDS `recent_events` hands back by default, and at most. The stream is read by
#: the record only when something is being chased, so the default is a screenful and the cap is
#: what one JSON answer can carry without becoming the 280 KB file it is a view of.
DEFAULT_LIMIT = 100
MAX_LIMIT = 2000


def recent_events(server: ServerConfig, *, limit: int = DEFAULT_LIMIT,
                  routine: str = "", event: str = "") -> dict:
    """The newest `limit` events of the stream itself, newest first — the records, not a fold.

    R2764: `.control/` is not listable for uid 1000 on this instance (the file inside it is
    0644), so `gu rsched logs` raises `PermissionError` and the documented way to inspect gate
    and fire events is unusable from inside EVERY routine run. The web process already parses
    this exact file for `blocked_fleet`, so serving the records costs no new reader and no new
    store — it makes the stream reachable the way everything else about a run is.

    Neither existing fold answers for it. `blocked_fleet` keeps only `BLOCKED_EVENTS` — a gate
    decision, a run start, a commit that landed are all dropped — and collapses what survives
    into one row per (event, subject), which is the opposite of what a reader asking "what
    happened at 03:00" needs. This filters instead of folding: `routine` and `event` narrow,
    and the answer is capped because the live stream is ~280 KB and grows forever.

    `total` counts everything that MATCHED, so a bounded answer says what it left out. There is
    no window: an event is asked for by name here, not by the clock, and a reader chasing a
    quiet failure wants the last one of its kind however old it is.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = [rec for rec in health_records(server.routines_home)
            if (not routine or str(rec.get("routine") or "") == routine)
            and (not event or str(rec.get("event") or "") == event)]
    # `health_records` hands back a SHARED list, oldest first, that callers must not mutate —
    # so the newest slice is taken by reversed slicing, never by reversing in place.
    return {"events": list(reversed(rows[-limit:])), "total": len(rows), "limit": limit}


def budget_endings(server: ServerConfig, slug: str, *,
                   days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """How this routine's partial finishes ended in the last `days`: `budget_exhausted` is
    the count a budget forced, `run_partial` the count the model chose with budget to spare.

    The usage stream files both as `partial`, so a routine that outgrew its ceiling and one
    that keeps finding a source down read identically there. `last_detail` names which budget
    (the event carries `resource`/`limit`), which is the sentence a reader needs.

    `corrected` is the third count (F637/F703): a leg that ended here, whose run was then
    resumed and finished `ok`. It leaves the other two rather than vanishing — the budget
    really did end that leg — but it is no longer read as how the RUN ended, which is the
    question this split answers.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    counts = dict.fromkeys(ENDING_EVENTS, 0)
    corrections = _corrections(server.routines_home)
    corrected = 0
    last_detail, last_ts, newest = "", "", None
    for when, rec in _window(server.routines_home, ENDING_EVENTS, _since(days)):
        if str(rec.get("routine") or "") != slug:
            continue
        if runcorrection.is_correctable(rec) and _corrected_after(
                corrections, str(rec.get("run_id") or ""), when):
            corrected += 1
            continue
        counts[str(rec["event"])] += 1
        if newest is None or when >= newest:
            newest, last_ts = when, str(rec.get("ts") or "")
            last_detail = str(rec.get("detail") or "")
    return {"window_days": days, **counts, "corrected": corrected,
            "last_ts": last_ts, "last_detail": last_detail}
