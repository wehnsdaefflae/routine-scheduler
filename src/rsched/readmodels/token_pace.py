"""How fast a routine is consuming TOKENS this month, and where that lands by month end.

The operator's ask: *"a token count on the routine overview page so we can anticipate our token
budget running out"*. The card already showed what a routine has SPENT (`api_routines._spend_line`
over `stats.monthly_spend`) — a total, which answers "what did this cost" and not "when do I run
out". This adds the two readings that do: the token COUNT beside the context volume, and a
straight-line PROJECTION of this month's tokens to the month's end.

What it deliberately does NOT do is claim a ceiling. Per-routine `max_total_tokens` defaults to
-1 (unlimited) and nothing on this instance sets it, so a "73% of budget" figure would be
dividing by a number that does not exist; and the ceiling that really bites — a subscription's
rolling window — belongs to the model gateway, which owns the logins (the operator's directive
moved every subscription fact there). So this reports a RATE and a DESTINATION and lets the
reader compare it with whatever ceiling they hold. A projection is honest only while it is
labelled as one: `projected` is always accompanied by `elapsed_frac`, the share of the month the
figure is extrapolated from, and no projection is offered at all before the month is 15% gone —
one heavy run on the 1st would otherwise "project" thirty times itself.

Source: `stats.monthly_spend`, already read ONCE per dashboard render for the spend line, so this
costs no extra parse. `tokens` there excludes the prompt cache (`volume` is the whole context the
model processed) — both are carried, because the one that runs a budget down is the one your
provider counts, and which that is depends on the route.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime

#: Below this share of the month elapsed, a straight-line projection says more about the calendar
#: than about the routine, so none is offered (the card shows the count and the trend instead).
MIN_ELAPSED_FRAC = 0.15

#: `volume` growth above this multiple of last month flags the card — the existing spend line's
#: own threshold, kept identical so two readings of the same series cannot disagree.
GROWING_FACTOR = 1.2


def _month_progress(month: str, now: datetime) -> float:
    """Share of `month` (YYYY-MM) already elapsed at `now` — 1.0 for any month already over.

    A past month is COMPLETE, which is what makes its figure a fact rather than a forecast; a
    future month cannot be reached (clock skew, a stale series) and is treated as not started.
    """
    try:
        year, mon = (int(part) for part in month.split("-", 1))
    except (ValueError, TypeError):
        return 1.0
    if (year, mon) < (now.year, now.month):
        return 1.0
    if (year, mon) > (now.year, now.month):
        return 0.0
    days = monthrange(year, mon)[1]
    elapsed = (now.day - 1) + (now.hour * 3600 + now.minute * 60 + now.second) / 86_400
    return max(0.0, min(1.0, elapsed / days))


def pace(monthly: dict, slug: str, *, now: datetime | None = None) -> dict | None:
    """This month's token pace for one routine — None when the series knows nothing about it.

    Shape: `{"month", "tokens", "volume", "notional", "runs", "elapsed_frac",
    "projected_tokens"|None, "projected_volume"|None, "prev_month"|None, "prev_tokens"|None,
    "growing": bool}`. Every token figure is an int and every dollar figure a float, so a
    renderer never has to guess; `projected_*` is None rather than 0 when the month is too young
    to extrapolate from — a 0 would render as "heading for nothing".
    """
    months = monthly.get("months") or []
    cells = (monthly.get("by_routine") or {}).get(slug) or {}
    if not months or not cells:
        return None
    month = months[-1]
    current = cells.get(month)
    if not current:
        return None
    prev_month = months[-2] if len(months) > 1 else None
    prev = cells.get(prev_month) if prev_month else None
    frac = _month_progress(month, now or datetime.now(UTC))
    tokens = int(current.get("tokens") or 0)
    volume = int(current.get("volume") or 0)
    project = frac >= MIN_ELAPSED_FRAC and frac > 0
    return {
        "month": month,
        "tokens": tokens,
        "volume": volume,
        "notional": float(current.get("notional") or 0.0),
        "runs": int(current.get("runs") or 0),
        "elapsed_frac": round(frac, 4),
        "projected_tokens": round(tokens / frac) if project else None,
        "projected_volume": round(volume / frac) if project else None,
        "prev_month": prev_month if prev else None,
        "prev_tokens": int(prev.get("tokens") or 0) if prev else None,
        "growing": bool(prev and volume > (prev.get("volume") or 0) * GROWING_FACTOR),
    }


def fleet_pace(monthly: dict, *, now: datetime | None = None) -> dict | None:
    """The same reading SUMMED over every routine — the instance's own token pace.

    One routine's month-end figure answers "is this one growing"; the fleet's answers the
    operator's actual question, because a subscription window is spent by every routine at once.
    """
    months = monthly.get("months") or []
    if not months:
        return None
    month = months[-1]
    rows = (monthly.get("by_routine") or {}).values()
    tokens = sum(int((cells.get(month) or {}).get("tokens") or 0) for cells in rows)
    volume = sum(int((cells.get(month) or {}).get("volume") or 0) for cells in rows)
    notional = sum(float((cells.get(month) or {}).get("notional") or 0.0) for cells in rows)
    frac = _month_progress(month, now or datetime.now(UTC))
    project = frac >= MIN_ELAPSED_FRAC and frac > 0
    return {
        "month": month,
        "tokens": tokens,
        "volume": volume,
        "notional": round(notional, 4),
        "elapsed_frac": round(frac, 4),
        "projected_tokens": round(tokens / frac) if project else None,
        "projected_volume": round(volume / frac) if project else None,
        "projected_notional": round(notional / frac, 2) if project else None,
    }
