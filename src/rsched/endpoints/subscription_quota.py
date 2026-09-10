"""Normalise Claude account windows independently of the authentication transport."""

import math
from datetime import UTC, datetime

WINDOWS = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus")


def normalize(raw: dict, *, now: datetime | None = None) -> dict:
    """The raw payload reduced to the windows we render. `utilization` is percent USED, so
    `remaining` is its complement — which is the number the operator actually asked for.
    """
    now = now or datetime.now(UTC)
    out: dict[str, dict] = {}
    for name in WINDOWS:
        bucket = raw.get(name)
        if not isinstance(bucket, dict) or bucket.get("utilization") is None:
            continue
        try:
            used = float(bucket["utilization"])
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(used) or not 0 <= used <= 100:
            continue
        row = {"utilization": round(used, 2), "remaining": round(100.0 - used, 2),
               "resets_at": bucket.get("resets_at") or "", "seconds_until_reset": None}
        if row["resets_at"]:
            try:
                # the API stamps are Z-suffixed; fromisoformat takes that since 3.11
                when = datetime.fromisoformat(str(row["resets_at"]))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                row["seconds_until_reset"] = max(0, int((when - now).total_seconds()))
            except ValueError:
                pass
        out[name] = row
    return out
