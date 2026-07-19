"""The dashboard week strip: every scheduled routine's fire times enumerated over the
coming days (croniter, each routine's own tz). A day of back-fill lets the client render
"earlier today" in its own timezone; a per-routine cap bounds every-minute crons.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import schedule_once
from ..daemon import registry

router = APIRouter(tags=["schedule"])

MAX_FIRES = 400  # per routine — hourly is ~192 with back-fill; denser crons truncate


@router.get("/schedule/week")
def schedule_week(request: Request, days: int = 7) -> dict:
    """Fire times for every enabled routine from a day ago to `days` (1-14) ahead:
    {start, days, routines: [{slug, fires: [iso…], one_shots: [iso…], truncated}]}.
    `fires` are recurring cron fires; `one_shots` are armed schedule-once fires in the
    window (the client renders them as distinct points). A routine with only a one-shot
    armed and no cron still appears.
    """
    days = max(1, min(days, 14))
    now = datetime.now(UTC)
    start, end = now - timedelta(days=1), now + timedelta(days=days)
    home = request.app.state.server.routines_home
    routines = []
    for info in registry.scan(request.app.state.server).values():
        cfg = info.cfg
        if not cfg.enabled:
            continue
        fires: list[str] = []
        if cfg.cron:
            try:
                it = croniter(cfg.cron, start.astimezone(ZoneInfo(cfg.tz)))
            except (ValueError, KeyError):
                it = None  # a broken cron/tz already surfaces as a routine problem
            if it is not None:
                while len(fires) < MAX_FIRES:
                    t = it.get_next(datetime)
                    if t >= end:
                        break
                    fires.append(t.isoformat())
        one_shots = _one_shot_fires(home, cfg.slug, start, end)
        if fires or one_shots:
            routines.append({"slug": cfg.slug, "fires": fires, "one_shots": one_shots,
                             "truncated": len(fires) >= MAX_FIRES})
    return {"start": now.isoformat(), "days": days, "routines": routines}


def _one_shot_fires(routines_home, slug: str, start: datetime, end: datetime) -> list[str]:
    """Armed one-shot fire instants for `slug` inside [start, end), UTC-normalised & sorted."""
    out: list[str] = []
    for p in schedule_once.pending_requests(routines_home, slug):
        r = schedule_once.read_request(p)
        raw = str(r.get("fire_at") or "")
        if not r.get("active", True) or not raw:
            continue
        try:
            t = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        t = t.astimezone(UTC)
        if start <= t < end:
            out.append(t.isoformat())
    return sorted(out)


# -- one-shot time triggers (the routine page's Schedule-once card) -----------------------
# The web layer only RECORDS a request in the spool (rsched.schedule_once); the daemon's
# OneShotManager fires it once then consumes it. This is the user/UI arming path — a routine
# arms via the gated `schedule_run` action. Both write the same spool.


class ScheduleOnceCreate(BaseModel):
    fire_at: str
    reason: str = ""


def _require_routine(request: Request, slug: str) -> None:
    if slug not in registry.scan(request.app.state.server):
        raise HTTPException(404, f"no routine {slug!r}")


@router.post("/routines/{slug}/schedule-once", status_code=201)
def arm_schedule_once(request: Request, slug: str, body: ScheduleOnceCreate) -> dict:
    """Arm a one-shot future run of the routine. 404 unknown routine, 422 bad fire_at."""
    server = request.app.state.server
    _require_routine(request, slug)
    try:
        fire_at = schedule_once.parse_fire_at(body.fire_at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    rec = schedule_once.arm(server.routines_home, slug, fire_at=fire_at,
                            reason=body.reason, requested_by="ui")
    return {"ok": True, "one_shot": rec}


@router.get("/routines/{slug}/schedule-once")
def list_schedule_once(request: Request, slug: str) -> dict:
    """The armed one-shots + the daemon fire ledger for the routine page card."""
    _require_routine(request, slug)
    return schedule_once.describe(request.app.state.server.routines_home, slug)


@router.delete("/routines/{slug}/schedule-once/{req_id}")
def cancel_schedule_once(request: Request, slug: str, req_id: str) -> dict:
    """Cancel one armed one-shot by id (delete its request file). 404 if it is not armed."""
    server = request.app.state.server
    if schedule_once.cancel(server.routines_home, slug, req_id) == 0:
        raise HTTPException(404, f"no armed one-shot {req_id!r} on {slug!r}")
    return {"ok": True, "cancelled": 1}
