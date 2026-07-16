"""Friendly schedule ↔ cron translation, so the UI never shows raw cron or asks for a tz.

Friendly spec (what the UI sends/receives):
  {"frequency": "manual|hourly|daily|weekly|monthly",
   "time": "HH:MM",        # daily/weekly/monthly (local time)
   "minute": 0-59,          # hourly
   "weekday": 0-6,          # weekly (0=Sunday … 6=Saturday)
   "day": 1-31}             # monthly

The routine still stores a cron string (croniter drives the scheduler); this module is the
single source of truth for the round-trip. Timezone is the server's local zone — set once at
load, never surfaced to the user.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def server_tz() -> str:
    """The server's local IANA timezone name (e.g. 'Europe/Berlin'), best-effort. Inside
    a container the host's zone arrives as a TZ env var or a bind-mounted /etc/timezone
    (a plain file naming the zone) — /etc/localtime stops being a readable symlink there,
    so all three routes are tried.
    """
    env = os.environ.get("TZ", "").strip().lstrip(":")
    if env:
        return env
    try:
        tz = datetime.now(UTC).astimezone().tzinfo
        key = getattr(tz, "key", None)
        if key:
            return str(key)
        link = Path("/etc/localtime")
        if link.is_symlink():
            p = str(link.resolve())
            if "zoneinfo/" in p:
                return p.split("zoneinfo/", 1)[1]
        tzfile = Path("/etc/timezone")
        if tzfile.is_file():
            name = tzfile.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    return "UTC"


def friendly_to_cron(spec: dict) -> str:
    """Friendly spec → cron string ('' for manual). Raises ValueError on bad input."""
    freq = (spec or {}).get("frequency", "manual")
    if freq == "manual":
        return ""
    if freq == "hourly":
        minute = int(spec.get("minute", 0))
        _check(0 <= minute <= 59, "minute must be 0-59")
        return f"{minute} * * * *"
    hh, mm = _parse_time(spec.get("time", "07:00"))
    if freq == "daily":
        return f"{mm} {hh} * * *"
    if freq == "weekly":
        wd = int(spec.get("weekday", 1))
        _check(0 <= wd <= 6, "weekday must be 0-6")
        return f"{mm} {hh} * * {wd}"
    if freq == "monthly":
        day = int(spec.get("day", 1))
        _check(1 <= day <= 31, "day must be 1-31")
        return f"{mm} {hh} {day} * *"
    raise ValueError(f"unknown frequency {freq!r}")


def cron_to_friendly(cron: str) -> dict:
    """Cron string → friendly spec. Unrecognized crons come back as
    {'frequency': 'custom', 'cron': <raw>} so the UI can show them read-only.
    """
    cron = (cron or "").strip()
    if not cron:
        return {"frequency": "manual"}
    parts = cron.split()
    if len(parts) != 5:
        return {"frequency": "custom", "cron": cron}
    mn, hr, dom, mon, dow = parts
    try:
        if mon == "*" and dom == "*" and dow == "*" and hr == "*" and mn.isdigit():
            return {"frequency": "hourly", "minute": int(mn)}
        if mon == "*" and mn.isdigit() and hr.isdigit():
            time = f"{int(hr):02d}:{int(mn):02d}"
            if dom == "*" and dow == "*":
                return {"frequency": "daily", "time": time}
            if dom == "*" and dow.isdigit():
                return {"frequency": "weekly", "time": time, "weekday": int(dow)}
            if dow == "*" and dom.isdigit():
                return {"frequency": "monthly", "time": time, "day": int(dom)}
    except ValueError:
        pass
    return {"frequency": "custom", "cron": cron}


def describe(cron: str) -> str:
    """Human sentence for a cron, using the friendly spec."""
    f = cron_to_friendly(cron)
    freq = f["frequency"]
    if freq == "manual":
        return "Manual — runs only when you click Run now"
    if freq == "hourly":
        return f"Every hour at :{f['minute']:02d}"
    if freq == "daily":
        return f"Every day at {f['time']}"
    if freq == "weekly":
        return f"Every {WEEKDAYS[f['weekday']]} at {f['time']}"
    if freq == "monthly":
        return f"Every month on day {f['day']} at {f['time']}"
    return f"Custom schedule ({f.get('cron')})"


def _parse_time(t: str) -> tuple[int, int]:
    try:
        hh, mm = str(t).split(":")
        h, m = int(hh), int(mm)
        _check(0 <= h <= 23 and 0 <= m <= 59, "time must be HH:MM")
        return h, m
    except (ValueError, AttributeError):
        raise ValueError(f"bad time {t!r} (expected HH:MM)") from None


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)
