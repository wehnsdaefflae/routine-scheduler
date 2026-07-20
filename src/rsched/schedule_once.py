"""Schedule-once — the one-shot time-trigger request spool + fire ledger.

A one-shot fires a routine ONCE at a future instant, then never again — the missing case
between cron (repeats forever) and a manual run (now). It is armed either from the routine
page (POST /api/routines/<slug>/schedule-once) or by a ROUTINE holding the `scheduling`
capability (the `schedule_run` engine action), and it may target another routine — the
reviewer's ask: settable / cancellable by another routine.

Ownership mirrors event triggers (rsched.triggers) and restart.request: a one-shot that a
*routine* must be able to arm CANNOT live in routine.yaml (config is the user's, never
run-written), so it lives in a daemon-owned request spool the web layer AND the engine may
write and the daemon consumes:

    <routines_home>/.control/schedule-once/<slug>/req-<id>.json   # one armed one-shot
    <routines_home>/.control/schedule-once/<slug>/state.json      # daemon fire ledger

The DAEMON's OneShotManager (daemon/schedule_once.py) turns due requests into fires on the
scheduler tick — so run spawning, one-run-per-routine, max_concurrent_runs and the restart
drain stay the daemon's job, exactly as for cron and trigger fires. Auto-deactivate = the
manager DELETES the request file on a successful fire (consumption IS the non-repeating
guarantee — no routine.yaml rewrite, no self-disabling cron). Full spec: docs/schedule-once.md.

This module is the shared vocabulary both sides import: fire-time parsing, id generation,
and the spool file IO — the same split rsched.triggers uses for event triggers.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, read_json

# A relative fire-at like "+3d" / "+2h" / "+30m" / "+45s" — the common "re-check in N" case.
_REL = re.compile(r"^\+\s*(\d+)\s*([dhms])$", re.IGNORECASE)
_REL_UNIT = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
MAX_HORIZON = timedelta(days=366)   # a one-shot armed more than ~a year out is a typo, not a plan


def new_id() -> str:
    """A stable one-shot handle — server/engine-generated, never client-supplied."""
    return f"so-{uuid.uuid4().hex[:8]}"


def parse_fire_at(value: str, now: datetime | None = None) -> datetime:
    """Coerce a fire-at spec to an aware UTC instant. Accepts an absolute ISO-8601 datetime
    (naive is read as UTC) or a relative offset from now ("+3d", "+2h", "+30m", "+45s").
    Raises ValueError on an unparseable, past, or absurdly-far spec — the caller surfaces it.
    """
    now = now or datetime.now(UTC)
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("fire_at is required (an ISO instant or a relative offset like '+3d')")
    if m := _REL.match(raw):
        fire = now + timedelta(**{_REL_UNIT[m.group(2).lower()]: int(m.group(1))})
    else:
        try:
            fire = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                f"fire_at {raw!r} is not an ISO-8601 instant or a relative offset "
                "like '+3d'/'+2h'/'+30m'") from exc
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=UTC)
    fire = fire.astimezone(UTC)
    if fire <= now:
        raise ValueError(f"fire_at {fire.isoformat()} is not in the future")
    if fire - now > MAX_HORIZON:
        raise ValueError(f"fire_at {fire.isoformat()} is more than a year out — likely a typo")
    return fire


# -- the request spool ------------------------------------------------------------------
# Web / engine writes req-*.json (one per armed one-shot), the daemon consumes them on fire
# and writes state.json. Lives under the .control dot-dir the registry scan ignores.


def spool_dir(routines_home: Path, slug: str) -> Path:
    return routines_home / ".control" / "schedule-once" / slug


def arm(routines_home: Path, slug: str, *, fire_at: datetime, reason: str,
        requested_by: str, expires_at: datetime | None = None) -> dict:
    """Record one armed one-shot durably (atomic). Returns the stored record (with its id).
    `fire_at` must already be an aware UTC instant (see parse_fire_at).
    """
    rec = {
        "id": new_id(),
        "fire_at": fire_at.astimezone(UTC).isoformat(),
        "active": True,
        "reason": str(reason or ""),
        "requested_by": str(requested_by or ""),
        "created": now_iso(),
        "expires_at": expires_at.astimezone(UTC).isoformat() if expires_at else None,
    }
    atomic_write_json(spool_dir(routines_home, slug) / f"req-{rec['id']}.json", rec)
    return rec


def pending_requests(routines_home: Path, slug: str) -> list[Path]:
    """Armed request files, oldest-created first (the id suffix keeps them distinct)."""
    d = spool_dir(routines_home, slug)
    return sorted(d.glob("req-*.json")) if d.is_dir() else []


def read_request(path: Path) -> dict:
    r = read_json(path)
    return r if isinstance(r, dict) else {}


def request_path(routines_home: Path, slug: str, req_id: str) -> Path:
    return spool_dir(routines_home, slug) / f"req-{req_id}.json"


def cancel(routines_home: Path, slug: str, req_id: str | None = None) -> int:
    """Cancel = delete the request file(s). By id, or ALL armed on the slug when id is None.
    Idempotent; returns how many were removed.
    """
    if req_id is not None:
        p = request_path(routines_home, slug, req_id)
        if p.exists():
            p.unlink()
            return 1
        return 0
    removed = 0
    for p in pending_requests(routines_home, slug):
        p.unlink(missing_ok=True)
        removed += 1
    return removed


def slugs_with_requests(routines_home: Path) -> list[str]:
    root = routines_home / ".control" / "schedule-once"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(d.glob("req-*.json")))


def read_state(routines_home: Path, slug: str) -> dict:
    """The daemon-maintained fire ledger: {last_fired, fires}."""
    st = read_json(spool_dir(routines_home, slug) / "state.json")
    return st if isinstance(st, dict) else {}


def write_state(routines_home: Path, slug: str, state: dict) -> None:
    atomic_write_json(spool_dir(routines_home, slug) / "state.json", state)


def describe(routines_home: Path, slug: str) -> dict:
    """The routine page's Schedule-once card: the armed one-shots + the fire ledger."""
    armed = []
    for p in pending_requests(routines_home, slug):
        r = read_request(p)
        if r:
            armed.append({"id": str(r.get("id")), "fire_at": str(r.get("fire_at") or ""),
                          "active": bool(r.get("active", True)),
                          "reason": str(r.get("reason") or ""),
                          "requested_by": str(r.get("requested_by") or ""),
                          "created": str(r.get("created") or ""),
                          "expires_at": r.get("expires_at")})
    armed.sort(key=lambda a: str(a["fire_at"]))
    state = read_state(routines_home, slug)
    return {"armed": armed,
            "last_fired": str(state.get("last_fired") or ""),
            "fires": int(state.get("fires") or 0)}
