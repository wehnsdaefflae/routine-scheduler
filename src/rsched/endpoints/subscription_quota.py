"""Normalise Claude account windows independently of the authentication transport.

TWO SHAPES, ONE REPRESENTATION (D277). `/api/oauth/usage` used to carry every window as a
top-level key, and this module read four of them by name. The API is moving model- and
surface-scoped weekly limits into a `limits[]` array — entries
`{kind: "weekly_scoped", percent, resets_at, scope: {model|surface: {id, display_name}}}` —
while still sending some as legacy `seven_day_<model>` keys. So a scoped limit that exists only
in `limits[]` reached a reader that could not see it, and the card's weekly Opus/Sonnet line
would simply disappear: **a limit we cannot read reads as a limit that is not there**, which is
the one-authoritative-source rule broken in the direction that reassures.

The reconciliation is keyed by SCOPE, not by payload position, so a limit sent BOTH ways is one
row rather than two rows disagreeing. `ROW_KEYS` is the shape every row carries whichever shape
it arrived in, and `scoped` is where a reader finds the rows the fixed label map never had names
for — `quota.js` renders those from their OWN names.

An entry is validated ON ITS OWN. A malformed or newly shaped one costs THAT entry and never
the windows already parsed: the operator's quota readout must not go blank because one row of a
moving upstream payload grew a field. And an entry whose `kind` we do not know is REPORTED as an
unknown limit (named, no figure) rather than dropped — the same reason `health_stream` keeps a
vocabulary: a row nobody renders is a row nobody knows exists.
"""

import math
from datetime import UTC, datetime

#: The two UNSCOPED windows, read from their own top-level keys. These are the account's, not
#: any model's, and no `limits[]` entry replaces them.
WINDOWS = ("five_hour", "seven_day")

#: Reading the legacy `seven_day_<model>` keys at all is a BACK-COMPAT path, and this repo makes
#: every one of those declare when it is overdue for deletion — MIGRATION(expires=2026-12-31).
#: It exists because the upstream sends SOME scoped limits both ways during the move; once
#: `limits[]` carries them all, `_from_legacy` and this constant go, and the three tests named
#: for legacy keys go with them. The date is a prompt to re-read the live payload, not a promise
#: the upstream will be finished — if it is still dual-sending then, move the date and say so.
#:
#: The legacy top-level keys for what are now SCOPED weekly limits. Each is read as a scoped row
#: keyed by its model, so the same limit arriving in `limits[]` reconciles onto it instead of
#: producing a second row. Paseo also sees `seven_day_omelette`, so the prefix is what matters
#: and not this list: any `seven_day_<x>` key is read as the weekly limit of model `<x>`.
LEGACY_SCOPED_PREFIX = "seven_day_"

#: The `limits[]` entry kind this module understands. An entry of any other kind is reported as
#: unknown rather than dropped.
KIND_WEEKLY_SCOPED = "weekly_scoped"

#: A SCOPED ROW carries `dimension` (`model` or `surface` — the API scopes a limit by one or the
#: other, and a reader that assumed "model" would mislabel a surface limit as a model's), `id`,
#: `name`, `used_pct`, `remaining`, `resets_at` and `seconds_until_reset`. Stated here as prose
#: rather than as a tuple nothing reads: a key list with no consumer is documentation pretending
#: to be code, and the dead-code gate says so.


def _used(value: object) -> float | None:
    """A utilization percentage, or None when it is not one. 0-100 inclusive, finite."""
    try:
        used = float(value)                                 # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return used if math.isfinite(used) and 0 <= used <= 100 else None


def _reset_seconds(resets_at: str, now: datetime) -> int | None:
    """Seconds until `resets_at`, or None when it names no instant.

    None rather than 0: "resets now" and "carries no reset stamp" are different facts, and the
    card renders the second as nothing instead of as an expiry that has just passed.
    """
    if not resets_at:
        return None
    try:
        # the API stamps are Z-suffixed; fromisoformat takes that since 3.11
        when = datetime.fromisoformat(str(resets_at))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0, int((when - now).total_seconds()))


def _window_row(used: float, resets_at: str, now: datetime) -> dict:
    """One unscoped window. `utilization` is percent USED, so `remaining` is its complement —
    which is the number the operator actually asked for.
    """
    return {"utilization": round(used, 2), "remaining": round(100.0 - used, 2),
            "resets_at": resets_at, "seconds_until_reset": _reset_seconds(resets_at, now)}


def _scoped_row(*, dimension: str, ident: str, name: str, used: float, resets_at: str,
                now: datetime) -> dict:
    return {"dimension": dimension, "id": ident, "name": name or ident,
            "used_pct": round(used, 2), "remaining": round(100.0 - used, 2),
            "resets_at": resets_at,
            "seconds_until_reset": _reset_seconds(resets_at, now)}


def _from_limits(raw: dict, now: datetime) -> tuple[dict[str, dict], list[str]]:
    """The `limits[]` array as scoped rows keyed `<dimension>:<id>`, plus unknown-kind names.

    Each entry stands alone: a `TypeError` or a missing `scope` costs that entry. The unknown
    list carries a NAME per entry we could not read, so the card can say "there is a limit here
    we do not understand" instead of implying there is none.
    """
    rows: dict[str, dict] = {}
    unknown: list[str] = []
    for entry in raw.get("limits") or []:
        if not isinstance(entry, dict):
            unknown.append("a limit entry that is not an object")
            continue
        kind = str(entry.get("kind") or "")
        raw_scope = entry.get("scope")
        scope: dict = raw_scope if isinstance(raw_scope, dict) else {}
        dimension = next((d for d in ("model", "surface") if isinstance(scope.get(d), dict)), "")
        target: dict = scope.get(dimension) or {} if dimension else {}
        ident = str(target.get("id") or "")
        name = str(target.get("display_name") or "")
        if kind != KIND_WEEKLY_SCOPED or not dimension or not ident:
            unknown.append(name or ident or kind or "an unnamed limit")
            continue
        used = _used(entry.get("percent"))
        if used is None:
            unknown.append(name or ident)
            continue
        rows[f"{dimension}:{ident}"] = _scoped_row(
            dimension=dimension, ident=ident, name=name, used=used,
            resets_at=str(entry.get("resets_at") or ""), now=now)
    return rows, unknown


def _from_legacy(raw: dict, now: datetime) -> dict[str, dict]:
    """Every `seven_day_<model>` top-level key as a scoped row keyed `model:<model>`.

    By PREFIX rather than by a fixed list: the upstream adds models (Paseo sees
    `seven_day_omelette`), and a list is a thing that silently stops covering the payload.
    """
    rows: dict[str, dict] = {}
    for key, bucket in raw.items():
        if not key.startswith(LEGACY_SCOPED_PREFIX) or not isinstance(bucket, dict):
            continue
        model = key[len(LEGACY_SCOPED_PREFIX):]
        used = _used(bucket.get("utilization"))
        if not model or used is None:
            continue
        rows[f"model:{model}"] = _scoped_row(
            dimension="model", ident=model, name=model, used=used,
            resets_at=str(bucket.get("resets_at") or ""), now=now)
    return rows


def normalize(raw: dict, *, now: datetime | None = None) -> dict:
    """The raw payload reduced to what we render: `windows` (the two unscoped ones, by name),
    `scoped` (the weekly per-model/per-surface limits, keyed `<dimension>:<id>`) and `unknown`
    (named limits we could not read).

    A limit sent in BOTH shapes is ONE row: `limits[]` wins, because that is where the upstream
    is moving and it carries the display name and the dimension that the legacy key cannot.
    """
    now = now or datetime.now(UTC)
    windows: dict[str, dict] = {}
    for name in WINDOWS:
        bucket = raw.get(name)
        if not isinstance(bucket, dict):
            continue
        used = _used(bucket.get("utilization"))
        if used is None:
            continue
        windows[name] = _window_row(used, str(bucket.get("resets_at") or ""), now)
    scoped, unknown = _from_limits(raw, now)
    for key, row in _from_legacy(raw, now).items():
        scoped.setdefault(key, row)          # `limits[]` wins; the legacy key fills the gaps
    return {"windows": windows, "scoped": scoped, "unknown": unknown}
