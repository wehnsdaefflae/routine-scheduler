"""The between-runs prompt-cache gap, per routine — what a COLD start costs on every fire.

F507 / D163. Every other cost view in this package reads WITHIN a run: `stats.aggregate` rolls
up each run's own usage, `endpoints.usage.cache_read_share` and the `cache_read_degraded` health
event judge a finished run's internal cache discipline. None of them sees the interval BETWEEN
two fires of the same routine, which is where the provider's prompt cache expires: turn 1 of the
next fire re-writes the whole system+recipe+digest prefix at the cache-WRITE rate instead of
reading it back at the cache-READ rate. That difference is billed on every scheduled fire and
nothing measured it.

## What a cold start looks like in the record

A run's first metered turn is the whole prefix and almost no output. Measured on
`scheduler-builder:20261025-140002` turn 1: `cache_write: 53992`, no `cached_in`,
`notional: 0.34391` — and 53992 x 6.25/1e6 + 258 x 25/1e6 = 0.34391, the engine's own recorded
figure. Turn 2 of the same run reads those 53,992 tokens back. So, on turn 1:

- `cached_in` present — the prefix was still in the provider's cache: a WARM fire.
- `cache_write` and no `cached_in` — the cache had expired: a COLD fire, whose premium is
  `cache_write x (cache_write_rate - cache_read_rate)`.
- neither — the endpoint reports no cache traffic at all. That is NOT a warm fire (absence is
  not a hit, see `endpoints.usage.cache_read_share`): counted apart and never priced.
- turn 1 UNREADABLE — no transcript, a head of pure noise, an unreadable file. The gap is
  UNDETERMINED for that fire, and says so. It still counts as a fire, because a cold share
  computed against a denominator that dropped its own failures reads as a complete measurement
  when it is a partial one. 4 of the 686 retained run dirs are undetermined on 2026-10-26.

`premium_usd` is an UPPER BOUND on what keeping the cache warm could save, for one measured
reason: a warm turn 1 on this instance still writes most of its prefix anyway — measured
2026-10-26 over all 211 warm fires retained, `warm_read_share` is 0.3216, i.e. two thirds of a
warm first turn's cache traffic is still a WRITE. So the real counterfactual is cheaper than a
cold start, not free. The figure is what the cold re-write was BILLED at above a cache read —
not a saving anyone has banked.

What this view does NOT price, deliberately: the 8.05M tokens that warm fires re-wrote on their
first turn (2026-10-26). Part of that is prefix the cache lost and part is genuinely new
content — a recipe edit, a fuller digest, a bigger inbox — and a usage row cannot tell the two
apart. `warm_write_tokens` carries the quantity so the unpriced remainder is visible; it is
never folded into the premium, because what is not determinable is not estimated
(`endpoints.prices`' rule, applied to tokens instead of rates).

## Why the transcript, and not the usage stream

`.control/workflow-usage.jsonl` carries a LEG's summed cache traffic, so a run's cold first
write cannot be separated there from its own later re-reads. The transcript's first `usage` row
is the only record of turn 1. Prices come from the engine's own recorded table
(`endpoints.prices.price_for` over `.control/model-prices.json`) — a sandboxed run cannot read
the server config, so nothing here depends on a configured model's price being readable.

Only the HEAD of each transcript is parsed — but a GENEROUS head: turn 1 is usually line 2 and
is sometimes line 34, so `HEAD_LINES` is set from the measured distribution rather than from
what the shape suggests (see the constant). Memoized behind the same (inode, mtime_ns, size)
fingerprint the rest of this package uses, so a terminal transcript is read once per process
rather than per request.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from ..config import ServerConfig
from ..endpoints.prices import price_for
from . import memo

#: How many lines of a transcript's head may be read looking for the first metered turn.
#: MEASURED over this instance's 676 retained metered runs on 2026-10-26: turn 1's row sits on
#: line 2 in 259 of them, and BEYOND LINE 8 IN 77 (11.4%) — pre-turn-1 `user_injection` (1479
#: of them across 675 runs), `error` and `answer` records push it down, as far as line 34. An
#: eight-line window dropped every one of those 77 fires, and dropped them biased toward the
#: routines with the most inbox traffic, which are the expensive ones. The window therefore
#: sits an order of magnitude above the measured worst case: reading 200 short lines of a head
#: costs nothing beside a silently partial measurement.
HEAD_LINES = 200

#: The token counts a usage row carries (`endpoints.prices.USAGE_KEYS`). A row holding any of
#: them is a metered turn — whether or not it also names its model.
_TOKEN_KEYS = ("in", "out", "cached_in", "cache_write")


def _empty() -> dict:
    return {"fires": 0, "cold": 0, "warm": 0, "nocache": 0, "undetermined": 0,
            "cold_tokens": 0, "warm_read_tokens": 0, "warm_write_tokens": 0,
            "premium_usd": 0.0, "unpriced": 0, "last_cold": ""}


def _open_head(path: Path):
    """The transcript's bytes, plain or rotated to `.gz`. Mirrors `engine.transcript`'s own
    opener; this module needs the head alone, and `read_events` parses to EOF.
    """
    rotated = path.with_suffix(path.suffix + ".gz")
    if not path.exists() and rotated.exists():
        return gzip.open(rotated, "rb")
    return path.open("rb")


def _read_first_usage(path: Path) -> dict | None:
    """The first METERED `usage` dict in a transcript's head, or None.

    Metered means it carries a token count, NOT that it names a model: six real runs on this
    instance (newsletter-digest/20260717-040002 and five token-lab runs from 2026-07) carry a
    turn-1 row with `cache_write` and no `model` — the attribution field was added later. Their
    cache STATE is plainly readable from the token counts even though their price is not, so
    requiring a model here dropped six fires to spare one unpriceable premium.

    Never raises: a run dir being written, a truncated line or a vanished transcript returns
    None, which the caller counts as UNDETERMINED. One unreadable run must not take the whole
    cost view down, and must not vanish from it either.
    """
    try:
        with _open_head(path) as fh:
            for _ in range(HEAD_LINES):
                line = fh.readline()
                if not line:
                    return None
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                usage = rec.get("usage") if isinstance(rec, dict) else None
                if isinstance(usage, dict) and any(usage.get(k) for k in _TOKEN_KEYS):
                    return usage
    except OSError:
        return None
    return None


def first_usage(path: Path) -> dict | None:
    """`_read_first_usage`, memoized while the transcript's fingerprint holds."""
    return memo.memoized(f"cache-gap-turn1:{path}", [path], lambda: _read_first_usage(path))


def _rates_for(home: Path, model: str) -> tuple[float | None, float | None]:
    """(cache_read_rate, cache_write_rate) per million tokens for a run's recorded
    `<endpoint>/<model>`, from the engine's own price cache. (None, None) when unpriced.
    """
    endpoint, sep, name = str(model).partition("/")
    if not sep:
        return None, None
    rates = price_for(home, endpoint, name)
    if rates is None:
        return None, None
    return rates[2], rates[3]


def _count(acc: dict, home: Path, usage: dict | None, ts: str) -> None:
    """Fold one fire's turn-1 usage into a routine's accumulator.

    `usage` is None when the fire's first metered turn could not be read at all — no
    transcript, a head of pure noise, an unreadable file. That fire is UNDETERMINED: it counts
    as a fire and is named as undetermined, because the alternative is a view whose cold share
    is computed against a denominator that silently excluded its own failures. A failed
    measurement must never read as a pass.
    """
    acc["fires"] += 1
    if usage is None:
        acc["undetermined"] += 1
        return
    written = int(usage.get("cache_write") or 0)
    read = int(usage.get("cached_in") or 0)
    if not written and not read:
        acc["nocache"] += 1
        return
    if read:
        acc["warm"] += 1
        acc["warm_read_tokens"] += read
        acc["warm_write_tokens"] += written
        return
    acc["cold"] += 1
    acc["cold_tokens"] += written
    acc["last_cold"] = max(acc["last_cold"], ts)
    cache_read, cache_write = _rates_for(home, str(usage.get("model") or ""))
    if cache_read is None or cache_write is None:
        acc["unpriced"] += 1
        return
    acc["premium_usd"] = round(acc["premium_usd"]
                               + written * (cache_write - cache_read) / 1_000_000, 6)


def _run_dirs(home: Path) -> list[tuple[str, str, Path]]:
    """(slug, run ts, run dir) for every retained top-level run under a routines home."""
    out: list[tuple[str, str, Path]] = []
    try:
        slugs = sorted(p for p in home.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return out
    for slug in slugs:
        runs = slug / "runs"
        try:
            entries = sorted(p for p in runs.iterdir() if p.is_dir())
        except OSError:
            continue
        out.extend((slug.name, p.name, p) for p in entries)
    return out


def cache_gap(server: ServerConfig) -> dict:
    """Per-routine between-runs cold-start premium, for the Stats tab's cost story.

    Shape: `{"by_routine": {slug: {fires, cold, warm, nocache, undetermined, cold_tokens,
    warm_read_tokens, warm_write_tokens, premium_usd, per_cold_fire, unpriced, last_cold,
    warm_read_share}}, "totals": {...}, "rows": n}`, routines ordered by `premium_usd`
    descending — the ranking the operator asked for.

    `fires` is EVERY retained run dir of the routine, so cold + warm + nocache + undetermined
    always equals it: a fire this view could not read stays in the denominator it belongs to.

    Subruns are left out: a child run's turn 1 inherits its parent's live session, so its first
    write is not a between-FIRES gap — `_run_dirs` enumerates `<slug>/runs/<ts>` only, never the
    `sub/<n>` dirs beneath them. Conversations are included as rows of their own, exactly as the
    rest of this tab counts them (`stats.aggregate` reads both homes).
    """
    homes = [Path(server.routines_home)]
    conversations = getattr(server, "conversations_home", None)
    if conversations:
        homes.append(Path(conversations))
    by_routine: dict[str, dict] = {}
    for home in homes:
        for slug, ts, run in _run_dirs(home):
            usage = first_usage(run / "transcript.jsonl")
            _count(by_routine.setdefault(slug, _empty()), Path(server.routines_home), usage, ts)
    totals = _empty()
    for acc in by_routine.values():
        for key, value in acc.items():
            if key == "last_cold":
                totals[key] = max(totals[key], value)
            else:
                totals[key] = round(totals[key] + value, 6) if isinstance(value, float) \
                    else totals[key] + value
    for acc in (*by_routine.values(), totals):
        acc["per_cold_fire"] = round(acc["premium_usd"] / acc["cold"], 6) if acc["cold"] else 0.0
        warm_total = acc["warm_read_tokens"] + acc["warm_write_tokens"]
        acc["warm_read_share"] = round(acc["warm_read_tokens"] / warm_total, 4) \
            if warm_total else None
    ordered = dict(sorted(by_routine.items(), key=lambda kv: -kv[1]["premium_usd"]))
    return {"by_routine": ordered, "totals": totals, "rows": len(ordered)}
