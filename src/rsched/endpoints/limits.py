"""What the PROVIDER says this model's limits are — discovered, not hand-entered.

Context capacity and output reservation are token counts end to end. Missing metadata
falls back to configured defaults; input occupancy is explicitly estimated by compaction.
How each provider is ASKED is `catalogs`; this module decides what the answers mean and keeps
them.

## The two knobs need OPPOSITE treatment

This is the trap in "max out the tokens", and it is worth stating plainly:

- **The input window is adopted verbatim.** Pure win — a bigger window is more context.
- **The output cap is NOT maxed out.** Providers validate `input + requested_output <= window`
  up front (a provider's oversize 400 says exactly that), and
  `compaction.window_ceiling_tokens` subtracts `max_tokens` from the input budget for the same
  reason. Kimi K3's real 943,718-token output limit would collapse the usable prompt to ~10% of
  its 1M window. So the output cap resolves to the provider's maximum, never above
  ENGINE_OUTPUT_CEILING — a ceiling on what THIS HARNESS needs for one JSON action plus
  reasoning, not a stand-in for what the model can do — and never above a quarter of the
  window (`_output_cap`), because the same arithmetic starves a SMALL window: a flat 32,000 on
  a 32k model left 768 tokens of prompt. A provider that publishes a window and no maximum gets
  the cap derived from the window the same way.

## Precedence, and why config still wins

`per-MODEL config` → `discovered` → `endpoint default` → `engine floor` (the chain
`EndpointRegistry.resolve` walks). An operator who types a number on a model is sizing DOWN
deliberately (a cost budget, a slow provider), and `engine/window.py` already promises to honour
that; discovery must not overrule it. What discovery replaces is the endpoint's value, which was
only ever a default a model inherits when it says nothing — "a guess on the endpoint" that now
means "ask the provider".

## Derived state, never config

The cache lives at `<routines_home>/.control/model-limits.json` — the pattern
`daemon/library_watch.py` sets for daemon-owned derived state, explicitly "never config". Nothing
here writes `config.yaml`: the web layer remains the only config writer, a run still writes no
config, and deleting this file costs one refresh. Resolution READS it and never fetches: `resolve`
is on the per-turn path and must not make a network call, so a miss is simply the next tier down.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..paths import atomic_write_json, read_json
from . import catalogs

log = logging.getLogger("rsched.limits")

LIMITS_FILE = Path(".control") / "model-limits.json"
#: How long a discovered figure is trusted before the daemon re-asks. Provider windows change on
#: the order of model releases, not hours.
TTL = timedelta(hours=24)
#: The most output tokens this harness ever needs in one completion: one JSON action, plus room
#: for a reasoning model to think and for an `llm` tool-call's answer. Deliberately NOT the
#: provider maximum — see the module docstring. 16k truncated `effort: max` turns; 32k has not.
ENGINE_OUTPUT_CEILING = 32_000
#: The output reservation never takes more than 1/OUTPUT_WINDOW_DIVISOR of the window, so the
#: prompt always keeps three quarters of it — above the uncached compaction gate (0.6) and just
#: under the cached one (0.8), so a small model compacts a little earlier instead of never
#: fitting. From a 128,000-token window up, ENGINE_OUTPUT_CEILING is the smaller of the two.
OUTPUT_WINDOW_DIVISOR = 4

#: Claude WINDOWS for the ids no configured provider publishes figures FOR — a subscription
#: proxy whose `/v1/models` carries `{id, object, created, owned_by}` and nothing else, which is
#: every anthropic-kind endpoint here. (Anthropic's OWN listing has published
#: `max_input_tokens`/`max_tokens` since 2026-03; read it the day a direct endpoint is
#: configured.) A static table is a guess with a longer half-life than the guess it replaces, so
#: it is kept HERE beside the refresh that reads it and its staleness is visible in Settings as
#: `source: table` rather than passing for a measurement. It carries no output maxima: a row
#: from it gets the cap derived from its window, like any provider that publishes none.
STATIC_WINDOWS: dict[str, int] = {
    # https://platform.claude.com/docs/en/about-claude/models/overview and the model
    # deprecations page (2026-10-01). A key matches every id that STARTS with it — a dated
    # snapshot (`claude-haiku-4-5-20251001`) and a later point revision of a 5-series family
    # (`claude-opus-5-5` under `claude-opus-5`, whose window every such revision has kept);
    # the 4-series changed window between revisions, so each is keyed on its own. Retired
    # models are not listed: a request to one fails whatever its window.
    "claude-opus-4-6": 1_000_000, "claude-opus-4-7": 1_000_000,
    "claude-opus-4-8": 1_000_000, "claude-opus-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000, "claude-sonnet-5": 1_000_000,
    "claude-fable-5": 1_000_000, "claude-mythos-5": 1_000_000,
    "claude-opus-4-5": 200_000, "claude-sonnet-4-5": 200_000, "claude-haiku-4-5": 200_000,
}

#: Cache key holding `{endpoint: [served ids]}`. Not a model row — see `_rows`.
SERVED_KEY = "served"


def cache_path(routines_home: Path) -> Path:
    return routines_home / LIMITS_FILE


def _key(endpoint: str, model: str) -> str:
    return f"{endpoint}|{model}"


def load(routines_home: Path) -> dict:
    data = read_json(cache_path(routines_home))
    return data if isinstance(data, dict) else {}


def lookup(routines_home: Path, endpoint: str, model: str) -> dict | None:
    """The discovered limits for one (endpoint, model), or None. Read-only and never fetches —
    this sits on the per-turn resolution path.
    """
    row = load(routines_home).get(_key(endpoint, model))
    return row if isinstance(row, dict) and row.get("context_tokens") else None


def _rows(cache: dict) -> dict:
    """The (endpoint, model) rows alone — the cache also carries `fetched`, `checked_models`
    and `served`, and every count of "how many models do we know" means the rows.
    """
    return {k: v for k, v in cache.items()
            if "|" in k and isinstance(v, dict)}


def serves(routines_home: Path, endpoint: str, model: str) -> bool | None:
    """Does this endpoint's provider actually LIST this model id? `None` when the provider
    publishes no catalog route (or it was unreachable at the last refresh) — unanswered, which
    must not be reported as a no. Read-only, like `lookup`.
    """
    served = load(routines_home).get(SERVED_KEY)
    ids = served.get(endpoint) if isinstance(served, dict) else None
    return model in ids if isinstance(ids, list) else None


def window_tokens(row: dict) -> int:
    """Provider context capacity in tokens, or zero for a metadata miss."""
    ctx = row.get("context_tokens")
    return int(ctx) if isinstance(ctx, int | float) and ctx > 0 else 0


def _static_window(model_id: str) -> int | None:
    low = model_id.lower()
    for prefix, window in STATIC_WINDOWS.items():
        if low.startswith(prefix) or f"/{prefix}" in low:
            return window
    return None


def _output_cap(window: int, published: int | None) -> int:
    """The output cap a discovered row resolves to: the provider's published maximum (or, when
    it publishes none, the engine's ceiling), never above ENGINE_OUTPUT_CEILING and never above
    a quarter of the window — see the module docstring and OUTPUT_WINDOW_DIVISOR.
    """
    return min(published or ENGINE_OUTPUT_CEILING, ENGINE_OUTPUT_CEILING,
               window // OUTPUT_WINDOW_DIVISOR)


# ------------------------------------------------------------------------------- the refresh ----

def refresh(server, *, force: bool = False) -> dict:
    """Re-ask every configured provider and rewrite the cache. Returns `{written, skipped,
    misses}`. A provider fault never raises: one that is down leaves the previous figures in
    place (only the cache write itself can fail, and the caller logs it).

    Called from the daemon tick behind the TTL, never from `resolve`. A Settings save that adds
    a model makes the cache stale at once (`_missing_models`), so the next tick asks.
    """
    home = server.routines_home
    cache = load(home)
    now = datetime.now(UTC)
    if not force and not _missing_models(server, cache) and cache.get("fetched"):
        try:
            if datetime.fromisoformat(str(cache["fetched"])) + TTL > now:
                return {"written": 0, "skipped": len(_rows(cache)), "misses": []}
        except ValueError:
            pass

    by_endpoint: dict[str, list[str]] = {}
    for mc in server.models.values():
        by_endpoint.setdefault(mc.endpoint, []).append(mc.model)

    out: dict = {"fetched": now.isoformat(),
                 "checked_models": [_key(mc.endpoint, mc.model) for mc in server.models.values()]}
    misses: list[str] = []
    cached_served = cache.get(SERVED_KEY)
    prev_served: dict = cached_served if isinstance(cached_served, dict) else {}
    served_out: dict[str, list[str]] = {}
    for ep_name, model_ids in sorted(by_endpoint.items()):
        ep = server.endpoints.get(ep_name)
        if ep is None:
            continue
        provider = catalogs.provider_of(ep)
        try:
            table, served = catalogs.read(provider, ep, model_ids)
        except Exception as exc:
            log.warning("limits: %s discovery failed: %s", ep_name, exc)
            table, served = {}, None
        # An UNANSWERED listing keeps the previous set: a provider that was down for one
        # refresh must never turn into "this endpoint does not serve your models".
        if served:
            served_out[ep_name] = sorted(served)
        elif kept := prev_served.get(ep_name):
            served_out[ep_name] = kept
        for mid in model_ids:
            hit = table.get(mid)
            if hit is None and (static := _static_window(mid)) is not None:
                hit, provider_used = (static, None), "table"
            else:
                provider_used = provider
            if hit is None:
                misses.append(f"{ep_name}/{mid}")
                # keep whatever we knew before rather than forgetting it on one bad fetch
                if prev := cache.get(_key(ep_name, mid)):
                    out[_key(ep_name, mid)] = prev
                continue
            ctx, max_out = hit
            out[_key(ep_name, mid)] = {
                "context_tokens": ctx,
                "max_output_tokens": _output_cap(ctx, max_out),
                "provider_max_output_tokens": max_out,
                "source": provider_used, "fetched": now.isoformat()}
    if served_out:
        out[SERVED_KEY] = served_out
    cache_path(home).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cache_path(home), out)
    written = len(_rows(out))
    if misses:
        log.info("limits: %d model(s) not listed by their provider: %s",
                 len(misses), ", ".join(misses))
    return {"written": written, "skipped": 0, "misses": misses}


def stale(server) -> bool:
    """Is the cache older than the TTL (or absent)? The daemon's tick check."""
    cache = load(server.routines_home)
    if _missing_models(server, cache):
        return True
    fetched = cache.get("fetched")
    if not fetched:
        return True
    try:
        return datetime.fromisoformat(str(fetched)) + TTL <= datetime.now(UTC)
    except ValueError:
        return True


def _missing_models(server, cache: dict) -> bool:
    """A fresh global timestamp must not hide newly added endpoint/model pairs."""
    checked = cache.get("checked_models", cache)
    return any(_key(mc.endpoint, mc.model) not in checked for mc in server.models.values())
