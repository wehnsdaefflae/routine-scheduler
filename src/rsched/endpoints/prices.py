"""What a completion costs in dollars — the NOTIONAL figure every cost ranking reads.

A subscription transport bills nothing per token, so the provider's own `cost` is 0.0 on every
run it serves — nearly every run on this instance. Ranking routines by that number, or by
`tokens` (fresh input + output, which leaves the prompt cache out), hid where the money went:
freelance-radar's last run read `tokens: 650,520, cost: 0.0` while it had pushed 40 million
cached input tokens through the model. This module gives every completion a dollar figure for
its WHOLE context volume — fresh input, output, cache reads and cache writes, each at its own
rate — whatever transport carried it.

## One source per model, decided by its ROUTE — never a chain of guesses

- **A metered route** — the provider bills this call — is priced by the provider: the `cost` it
  REPORTED on the completion when it reports one (OpenRouter does, the auto-router included,
  whose list price is variable), else the prices it PUBLISHES for that exact model id
  (OpenRouter's `/models`, Nano-GPT's own `/api/models`).
- **A subscription route** (a proxy whose catalog lists ids and no prices — every `anthropic`
  endpoint here, the codex and antigravity proxies) is priced at the model's LIST price, read
  from the public reference catalog (OpenRouter's `/models`) under the name vendors share
  (`price_name`): that is what the same tokens cost when bought, which is what "notional" means.

What is not published is NOT priced. A model no source prices, or a completion that used a token
class whose rate its source does not publish (a cache write on a model listing no cache-write
rate), gets no `notional` at all — it is UNPRICED, and `unpriced` names every such configured
model so the gap is visible. Nothing is estimated, defaulted or borrowed from another model.

## Kept fresh, recorded at the time

The rates are re-read from the providers on `limits.refresh`'s daily tick into
`<routines_home>/.control/model-prices.json` (derived state, never config). A completion is
priced the moment it finishes (`price_usage`, called at the instrumentation seam), so a run's
recorded figure is what its tokens cost THEN — a later price change never rewrites history.
`price_for` reads the cache and never fetches: it sits on the per-call path.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from ..paths import atomic_write_json, read_json
from . import catalogs

log = logging.getLogger("rsched.prices")

PRICES_FILE = Path(".control") / "model-prices.json"

#: The usage keys, in the order a rates list carries them (USD per million tokens). A rate a
#: source does not publish is None — never a guess.
USAGE_KEYS = ("in", "out", "cached_in", "cache_write")
Rates = tuple[float | None, float | None, float | None, float | None]

#: A trailing snapshot date (`claude-haiku-4-5-20251001`): the price belongs to the model.
_SNAPSHOT = re.compile(r"-\d{8}$")


def cache_path(routines_home: Path) -> Path:
    return routines_home / PRICES_FILE


def price_name(model_id: str) -> str:
    """The name a model is priced under in the reference catalog: the vendors' shared name
    (`catalogs.reference_name`), its snapshot date dropped and its version written with dashes —
    the proxy's `claude-haiku-4-5-20251001` and OpenRouter's `anthropic/claude-haiku-4.5` are the
    same model.
    """
    return _SNAPSHOT.sub("", catalogs.reference_name(model_id)).replace(".", "-")


def _per_mtok(value: object) -> float | None:
    """A catalog's per-TOKEN price string as dollars per million; None when absent, unparsable
    or negative (OpenRouter writes -1 for a price that varies per call).
    """
    if value is None:
        return None
    try:
        rate = float(str(value))
    except ValueError:
        return None
    return round(rate * 1_000_000, 6) if rate >= 0 else None


def _openrouter_rates(row: dict) -> Rates | None:
    pricing = row.get("pricing")
    if not isinstance(pricing, dict):
        return None
    rates = (_per_mtok(pricing.get("prompt")), _per_mtok(pricing.get("completion")),
             _per_mtok(pricing.get("input_cache_read")),
             _per_mtok(pricing.get("input_cache_write")))
    return rates if rates[0] is not None and rates[1] is not None else None


def _nanogpt_rates(row: dict) -> Rates | None:
    pricing = row.get("pricing")
    if not isinstance(pricing, dict):
        return None
    fresh, out = pricing.get("prompt_per_million"), pricing.get("completion_per_million")
    per_1k = pricing.get("cacheReadInputPer1kTokens")
    if not isinstance(fresh, int | float) or not isinstance(out, int | float):
        return None
    read = round(float(per_1k) * 1000, 6) if isinstance(per_1k, int | float) else None
    return (float(fresh), float(out), read, None)


def _model_rates(provider: str, model_id: str, *, reference: dict[str, dict],
                 by_name: dict[str, dict], nanogpt: dict[str, dict]) -> tuple[Rates | None, str]:
    """(rates, source) for one configured model, from the one source its route has."""
    if provider == "openrouter":
        row = reference.get(model_id)
        return (_openrouter_rates(row) if row else None), "provider"
    if provider == "nanogpt":
        row = nanogpt.get(model_id)
        return (_nanogpt_rates(row) if row else None), "provider"
    row = by_name.get(price_name(model_id))
    return (_openrouter_rates(row) if row else None), "reference"


def refresh(server, reference_body: dict | None) -> dict:
    """Re-read every configured model's prices into the cache. `reference_body` is the public
    catalog's listing as `limits.refresh` already read it. Returns `{priced, unpriced}`. A source
    that did not answer keeps that model's previous row: a published price does not stop being
    published because one fetch failed.
    """
    home = server.routines_home
    previous = read_json(cache_path(home))
    prev_rows = previous.get("rows", {}) if isinstance(previous, dict) else {}
    rows_in = [r for r in (reference_body or {}).get("data") or [] if isinstance(r, dict)]
    reference = {str(r.get("id")): r for r in rows_in}
    by_name = {price_name(str(r["id"])): r for r in rows_in
               if r.get("id") and ":" not in str(r["id"])}
    nanogpt: dict[str, dict] = {}
    rows: dict[str, dict] = {}
    unpriced: list[str] = []
    for mc in server.models.values():
        ep = server.endpoints.get(mc.endpoint)
        if ep is None:
            continue
        provider = catalogs.provider_of(ep)
        if provider == "nanogpt" and not nanogpt:
            nanogpt = catalogs.nanogpt_models(ep)
        answered = bool(nanogpt) if provider == "nanogpt" else bool(rows_in)
        key = f"{mc.endpoint}|{mc.model}"
        rates, source = _model_rates(provider, mc.model, reference=reference,
                                     by_name=by_name, nanogpt=nanogpt)
        if rates is not None:
            rows[key] = {"rates": list(rates), "source": source}
        elif not answered and key in prev_rows:
            rows[key] = prev_rows[key]
        else:
            unpriced.append(f"{mc.endpoint}/{mc.model}")
    cache_path(home).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cache_path(home), {"fetched": datetime.now(UTC).isoformat(),
                                         "rows": rows, "unpriced": unpriced})
    if unpriced:
        log.info("prices: %d configured model(s) have no published price: %s",
                 len(unpriced), ", ".join(unpriced))
    return {"priced": len(rows), "unpriced": unpriced}


def price_for(routines_home: Path | None, endpoint: str, model: str) -> Rates | None:
    """The rates for one (endpoint, model) from the cache, or None. Never fetches."""
    if routines_home is None:
        return None
    data = read_json(cache_path(routines_home))
    row = (data.get("rows") or {}).get(f"{endpoint}|{model}") if isinstance(data, dict) else None
    rates = row.get("rates") if isinstance(row, dict) else None
    if not isinstance(rates, list) or len(rates) != len(USAGE_KEYS):
        return None
    return (rates[0], rates[1], rates[2], rates[3])


def unpriced(routines_home: Path) -> list[str]:
    """Every configured `endpoint/model` the last refresh could not price."""
    data = read_json(cache_path(routines_home))
    names = data.get("unpriced") if isinstance(data, dict) else None
    return [str(n) for n in names] if isinstance(names, list) else []


def notional(usage: dict, rates: Rates | None) -> float | None:
    """What `usage` costs at `rates`, in dollars — None when any token class it used is
    unpriced: a partial sum would read as the whole price.
    """
    if rates is None:
        return None
    total = 0.0
    for key, rate in zip(USAGE_KEYS, rates, strict=True):
        count = int(usage.get(key) or 0)
        if not count:
            continue
        if rate is None:
            return None
        total += count * rate
    return round(total / 1_000_000, 6)


def price_usage(routines_home: Path | None, endpoint: str, model: str, usage: dict) -> None:
    """Stamp `usage["notional"]` in place — the one writer, called at the completion seam. A
    provider that REPORTED what it billed is taken at its word; otherwise the route's rates.
    """
    if usage.get("cost"):
        usage["notional"] = round(float(usage["cost"]), 6)
        return
    value = notional(usage, price_for(routines_home, endpoint, model))
    if value is not None:
        usage["notional"] = value
