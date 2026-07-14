"""The dashboard week strip: every scheduled routine's fire times enumerated over the
coming days (croniter, each routine's own tz). A day of back-fill lets the client render
"earlier today" in its own timezone; a per-routine cap bounds every-minute crons."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, Request

from ..daemon import registry

router = APIRouter(tags=["schedule"])

MAX_FIRES = 400  # per routine — hourly is ~192 with back-fill; denser crons truncate


@router.get("/schedule/week")
def schedule_week(request: Request, days: int = 7) -> dict:
    """Fire times for every enabled, cron-scheduled routine from a day ago to `days`
    (1-14) ahead: {start, days, routines: [{slug, fires: [iso…], truncated}]}."""
    days = max(1, min(days, 14))
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=1), now + timedelta(days=days)
    routines = []
    for info in registry.scan(request.app.state.server).values():
        cfg = info.cfg
        if not cfg.enabled or not cfg.cron:
            continue
        try:
            it = croniter(cfg.cron, start.astimezone(ZoneInfo(cfg.tz)))
        except (ValueError, KeyError):
            continue  # a broken cron/tz already surfaces as a routine problem
        fires: list[str] = []
        while len(fires) < MAX_FIRES:
            t = it.get_next(datetime)
            if t >= end:
                break
            fires.append(t.isoformat())
        routines.append({"slug": cfg.slug, "fires": fires,
                         "truncated": len(fires) >= MAX_FIRES})
    return {"start": now.isoformat(), "days": days, "routines": routines}
