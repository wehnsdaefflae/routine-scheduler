"""The usage-dict vocabulary and its arithmetic — what every adapter reports and how it adds up.

A `Completion.usage` reading uses these keys: "in" (fresh input) and "out" always; "cached_in"
(prompt-cache reads) and "cache_write" when reported; "cost" (real $) — what the provider
REPORTED it billed (`reported_cost`); "notional" ($ at list price, stamped by
endpoints/prices.py). Adapters keep cache traffic OUT of "in", so a token budget keeps its
meaning.

`reading` is where an adapter ASSEMBLES one of these dicts: each wire reads its own key names
and does its own cache arithmetic, then hands the counts here, so the shape, the zero-is-absent
rule and the reported bill have one implementation instead of one per adapter.

`dollars_spent` is the one reading of those keys AS MONEY — what a cost budget meters
(`budgets.max_cost`) and any live dollar figure reads; the budget check is not a second
definition of it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def fold_usage(total: dict, delta: dict) -> None:
    """Add one usage reading into a running total, IN PLACE, by the vocabulary above.

    Dollars round to six places on every add, not once at the end: totals are re-added across
    a resumed run's legs, where float error compounds.

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


def summed(*readings: dict) -> dict:
    """Several requests' usage as ONE reading, by `fold_usage` — a re-asked turn cost both
    requests, and a budget reading only the second would undercount it. Every key folds,
    `cost` included (as a float, never an integer).
    """
    total: dict = {"in": 0, "out": 0}
    for reading in readings:
        fold_usage(total, reading)
    return total


#: The usage-block keys a provider reports its bill under, in order of preference: OpenRouter's
#: usage accounting (`cost`, a number) and an Otari gateway's own pricing (`cost_usd`, a
#: six-decimal USD STRING, `"0.000000"` for a subscription model — which IS that call's bill).
_REPORTED_COST_KEYS = ("cost", "cost_usd")


def reported_cost(raw: dict) -> float | None:
    """The dollars a provider REPORTED it billed for one call, read from its raw usage block —
    what an adapter files as usage `cost`. The first key of `_REPORTED_COST_KEYS` that holds a
    finite, non-negative number (or a string spelling one) wins; None when none does. A missing
    or malformed figure is NO figure — never zero, never a guess — so the call is then priced at
    its route's list price instead (`prices.price_usage`).
    """
    for key in _REPORTED_COST_KEYS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            continue
        try:
            amount = Decimal(str(value).strip())
        except InvalidOperation:
            continue
        if amount.is_finite() and amount >= 0:
            return float(amount)
    return None


def reading(raw: dict, *, fresh_in: int, out: int, cached_in: int = 0,
            cache_write: int = 0) -> dict:
    """ONE completion's usage in the vocabulary above, from the counts its wire reported.

    Every adapter calls this: a reading is the same dict whichever endpoint produced it, so a
    budget, a cache-health figure and a cost all mean one thing across the fleet. What stays
    the adapter's own job is reading its wire's key NAMES and its cache arithmetic — Anthropic
    reports fresh input already excluding cache traffic, the OpenAI shape reports a prompt
    TOTAL the adapter subtracts from. What this owns is the shape: `in`/`out` always, cache
    traffic kept OUT of `in` and omitted when zero (absent and zero are the same thing here),
    and `cost` exactly when the provider reported a bill (`reported_cost` over `raw`).

    `fresh_in` is clamped at zero: a wire whose breakdown exceeds its own total must not
    produce a negative token count.
    """
    usage: dict = {"in": max(int(fresh_in), 0), "out": int(out)}
    if cached_in:
        usage["cached_in"] = int(cached_in)
    if cache_write:
        usage["cache_write"] = int(cache_write)
    if (cost := reported_cost(raw)) is not None:
        usage["cost"] = cost
    return usage


def dollars_spent(usage: dict | None) -> float:
    """What one usage reading (or a run's fold of them) COST in dollars — the figure a cost
    budget meters. 0.0 when the completion was neither billed nor priced.

    `notional` first, `cost` only as a fallback (F758): `notional` is stamped on EVERY
    completion at the one instrumentation seam (`prices.price_usage`), and equals a reported
    `cost` wherever one exists, since that seam takes a reported bill at its word. The
    fallback covers a reading that never passed the seam (a replayed leg, a test adapter).
    """
    usage = usage or {}
    for key in ("notional", "cost"):
        if usage.get(key):
            return float(usage[key])
    return 0.0


def cache_read_share(usage: dict | None) -> float | None:
    """Prompt-cache reads ÷ all cache traffic for one usage record — the ONE number that
    separates a healthy append-only run from a broken one, and the only one that does.

    Reads alone cannot: a transport that stops resuming its session still hits the static
    prefix while re-WRITING the whole conversation every turn at 1.25x instead of 0.1x.

    None when the transport reports no cache traffic at all — an endpoint that does not
    cache, or a run too short to have any. Absence is not a low share.
    """
    reads = int((usage or {}).get("cached_in") or 0)
    writes = int((usage or {}).get("cache_write") or 0)
    total = reads + writes
    return reads / total if total else None
