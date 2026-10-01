"""Friendly schedule ↔ cron translation, so the UI never shows raw cron or asks for a tz.

Friendly spec (what the UI sends/receives):
  {"frequency": "manual|hourly|daily|weekly|monthly",
   "time": "HH:MM",        # daily/weekly/monthly (local time)
   "minute": 0-59,          # hourly
   "weekdays": [0-6, ...],  # weekly — a SET of days (0=Sunday … 6=Saturday), e.g. Mon-Fri
   "day": 1-31}             # monthly

The routine still stores a cron string (croniter drives the scheduler); this module is the
single source of truth for the round-trip. Timezone is the server's local zone — set once at
load, never surfaced to the user.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

#: Where a host — or a container, through its read-only bind mounts — names its zone.
ETC_TIMEZONE = Path("/etc/timezone")
ETC_LOCALTIME = Path("/etc/localtime")


def server_tz() -> str:
    """The server's local IANA timezone name (e.g. 'Europe/Berlin'), best-effort, from the
    first of these that names a zone `ZoneInfo` loads: the TZ env var, /etc/timezone, the
    /etc/localtime symlink — else 'UTC'. Inside a container the host's zone arrives as a TZ
    env var or a bind-mounted /etc/timezone (a plain file naming the zone). /etc/timezone is
    consulted BEFORE the /etc/localtime symlink: Docker mounts through the image's symlink,
    leaving a stale symlink NAME over correct zone DATA — the symlink is only trustworthy
    where /etc/timezone is absent.

    A source that names no loadable zone FALLS THROUGH rather than winning: the answer is
    written beside every cron the console saves (routine.yaml, lanes.json), so the systemd
    idiom `TZ=:/etc/localtime`, a POSIX rule string or a typo must never become a zone no
    routine can load. A path is read for the key under its `zoneinfo/`, following a symlink
    the way the last source does.
    """
    for read in (_tz_env, _etc_timezone, _localtime_link):
        try:
            zone = zone_key(read())
        except (OSError, RuntimeError, ValueError):
            continue        # an unreadable source names nothing (RuntimeError: a symlink loop)
        if zone:
            return zone
    return "UTC"


def _tz_env() -> str:
    return os.environ.get("TZ", "")


def _etc_timezone() -> str:
    return ETC_TIMEZONE.read_text(encoding="utf-8")


def _localtime_link() -> str:
    return str(ETC_LOCALTIME) if ETC_LOCALTIME.is_symlink() else ""


def zone_key(name: str) -> str:
    """`name` as a key `ZoneInfo` loads, or "" when it names none — the ONE zone check every
    stored tz goes through (`lanes` degrades a hand-edited zone with it). The POSIX leading
    colon is dropped; a path becomes the key under its `zoneinfo/` (resolved first, so a
    symlink names its target's zone and a plain file names none).
    """
    name = name.strip().lstrip(":")
    if name.startswith("/"):
        resolved = str(Path(name).resolve())
        name = resolved.split("zoneinfo/", 1)[1] if "zoneinfo/" in resolved else ""
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ""
    return name


def friendly_to_cron(spec: dict | None) -> str:
    """Friendly spec → cron string ('' for manual). Raises ValueError on bad input — on
    EVERY bad input: the routine PATCH answers 400 for a ValueError and catches nothing else.
    """
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        # ValueError, not TypeError: the contract is one error for any bad spec, shape or value
        raise ValueError(f"a schedule spec is a mapping, got {spec!r}")  # noqa: TRY004
    freq = spec.get("frequency", "manual")
    if freq in ("manual", "disabled"):
        return ""
    if freq == "hourly":
        minute = _int(spec.get("minute", 0), "minute")
        _check(0 <= minute <= 59, "minute must be 0-59")
        return f"{minute} * * * *"
    hh, mm = _parse_time(spec.get("time", "07:00"))
    if freq == "daily":
        return f"{mm} {hh} * * *"
    if freq == "weekly":
        # A SET of weekdays (F347, user order 2026-08-15 — Google-Calendar-style "repeat
        # on: S M T W T F S"): one day is just a one-element set, "not on weekends" is
        # [1,2,3,4,5]. Cron carries it natively as a day-of-week list.
        days_in = spec.get("weekdays")
        if not isinstance(days_in, list) or not days_in:   # explicit — mypy can narrow this
            raise ValueError("weekly needs a non-empty weekdays list (0=Sunday … 6=Saturday)")
        days = sorted({_int(d, "a weekday") for d in days_in})
        _check(all(0 <= d <= 6 for d in days), "weekdays must be 0-6")
        return f"{mm} {hh} * * {','.join(str(d) for d in days)}"
    if freq == "monthly":
        day = _int(spec.get("day", 1), "day")
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
            if dom == "*" and (days := _parse_dow(dow)) is not None:
                return {"frequency": "weekly", "time": time, "weekdays": days}
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
        days = f["weekdays"]
        if days == [1, 2, 3, 4, 5]:
            return f"Every weekday at {f['time']}"
        names = [WEEKDAYS[d] for d in days]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"Every {joined} at {f['time']}"
    if freq == "monthly":
        return f"Every month on day {f['day']} at {f['time']}"
    return f"Custom schedule ({f.get('cron')})"


def _parse_dow(dow: str) -> list[int] | None:
    """A cron day-of-week field as a sorted weekday set, or None when it isn't one.
    Accepts what people actually write: '3', '1,3,5', '1-5', 'MON-FRI'-free digits only —
    names, steps and mixed forms stay 'custom' (they are cron-literate territory).
    """
    days: set[int] = set()
    for part in dow.split(","):
        if part.isdigit():
            days.add(int(part))
        elif "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit() and int(a) <= int(b)):
                return None
            days.update(range(int(a), int(b) + 1))
        else:
            return None
    if not days or not all(0 <= d <= 6 for d in days):
        return None
    return sorted(days)


def _parse_time(t: str) -> tuple[int, int]:
    try:
        hh, mm = str(t).split(":")
        h, m = int(hh), int(mm)
        _check(0 <= h <= 23 and 0 <= m <= 59, "time must be HH:MM")
        return h, m
    except (ValueError, AttributeError):
        raise ValueError(f"bad time {t!r} (expected HH:MM)") from None


def _int(value: Any, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number, got {value!r}") from None


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)
