"""The usage-dict vocabulary and its arithmetic — what every adapter reports and how it adds up.

A `Completion.usage` reading uses these keys: "in" (fresh input) and "out" always; "cached_in"
(prompt-cache reads) and "cache_write" when the transport reports them; "cost" (real $) when
known; "notional" ($ at list price, stamped by endpoints/prices.py). Adapters keep cache traffic
OUT of "in", so a token budget keeps its meaning.

This is a separate module beside `base.py` for the reason `media_types.py` is: it is pure —
dict arithmetic, no transport, no HTTP — and `base.py` is over the house size bar and may only
shrink.
"""

from __future__ import annotations


def fold_usage(total: dict, delta: dict) -> None:
    """Add one usage reading into a running total, IN PLACE, by the vocabulary above.

    Four hand-rolled versions of this existed — the turn sum, the run context's accumulator,
    the resumed-legs replay, and the per-completion fold — so growing the vocabulary meant
    remembering four places. Dollars round to six places on every add, not once at the end:
    totals are re-added across a resumed run's legs, where float error compounds.

    Zero and absent are the same thing here, so a delta carrying no cache traffic and no cost
    adds no key (an unpriced completion no `notional`). A total that must REPORT `in`/`out` —
    a run's status.json — seeds them first: `{"in": 0, "out": 0}`, then fold.
    """
    for key in ("in", "out", "cached_in", "cache_write"):
        if delta.get(key):
            total[key] = total.get(key, 0) + int(delta[key])
    for key in ("cost", "notional"):
        if delta.get(key):
            total[key] = round(total.get(key, 0.0) + float(delta[key]), 6)


def cache_read_share(usage: dict | None) -> float | None:
    """Prompt-cache reads ÷ all cache traffic for one usage record — the ONE number that
    separates a healthy append-only run from a broken one, and the only one that does.

    Reads alone cannot: when a transport stops resuming its session, the static
    system+tools prefix keeps hitting (so `cached_in` stays non-zero and volume looks
    normal) while the whole conversation is re-WRITTEN every turn at 1.25x instead of
    re-read at 0.1x. That is a 12.5x multiplier on the same work, and it ran unseen for
    four days in September 2026 until a weekly subscription limit was exhausted.

    None when the transport reports no cache traffic at all — an endpoint that does not
    cache, or a run too short to have any. Absence is not a low share.
    """
    reads = int((usage or {}).get("cached_in") or 0)
    writes = int((usage or {}).get("cache_write") or 0)
    total = reads + writes
    return reads / total if total else None
