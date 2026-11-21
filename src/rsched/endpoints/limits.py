"""What the ROUTE says a model's limits are — discovered, not hand-entered.

Context capacity and output reservation are token counts end to end. How each catalog is ASKED
is `catalogs` (an endpoint's own listing) and `routecatalogs` (the catalog of the route a gateway
sends a name to, `routes`); this module decides what the answers mean and keeps them.

## The route, never the model

A figure belongs to the ROUTE a call takes, not to the model's name (a subscription may serve an
id on a smaller window than the API). An endpoint behind a gateway (`EndpointConfig.gateway`) is
sized by the route each name resolves to; any other endpoint by its own listing; the static
Claude table answers for Anthropic's own API alone. Nothing falls back to a PUBLIC figure: a
route that publishes nothing stays UNKNOWN and the Settings card names the miss.

## Both knobs at the route's maximum

The window and the PUBLISHED output maximum are adopted verbatim
(`compaction.window_ceiling_tokens` subtracts the reservation, so a large one compacts earlier).
One guard: a published output figure that restates the window (OpenRouter's ⌊0.9 × window⌋
placeholder, or the window itself; `routecatalogs.is_placeholder`) is not a maximum — providers
validate `input + requested_output <= window` — so it is replaced by the figure the other
catalogs agree on, or left unknown. A row with a window and no maximum carries no output figure.

## Precedence

`per-MODEL config` → `discovered` → `endpoint default` → `engine floor`
(`EndpointRegistry.resolve`). A number typed on a model is deliberate; discovery replaces only
the endpoint's default.

## Derived state, never config

The cache is `<routines_home>/.control/model-limits.json`; deleting it costs one refresh.
`resolve` is on the per-turn path and only READS it — a miss is simply the next tier down.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..paths import atomic_write_json, expand, read_json
from . import catalogs, prices, routecatalogs, routes

log = logging.getLogger("rsched.limits")

LIMITS_FILE = Path(".control") / "model-limits.json"
#: How long a discovered figure is trusted before the daemon re-asks. An edited gateway routing
#: file makes the cache stale at once (`_routes_changed`).
TTL = timedelta(hours=24)

#: Claude WINDOWS on ANTHROPIC'S OWN API — a direct `kind: anthropic` endpoint, or a gateway's
#: `anthropic` route — whose listing needs a key only the gateway holds. Never a proxy's figure.
#: Shown in Settings as `source: table`; it carries no output maxima.
STATIC_WINDOWS: dict[str, int] = {
    # https://platform.claude.com/docs/en/about-claude/models/overview. A key matches every id
    # that STARTS with it (dated snapshots, 5-series point revisions); the 4-series changed
    # window between revisions, so each is keyed on its own. Retired models are not listed.
    "claude-opus-4-6": 1_000_000, "claude-opus-4-7": 1_000_000,
    "claude-opus-4-8": 1_000_000, "claude-opus-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000, "claude-sonnet-5": 1_000_000,
    "claude-fable-5": 1_000_000, "claude-mythos-5": 1_000_000,
    "claude-haiku-5": 1_000_000,
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


def _figures(table: dict, mid: str, provider: str, route: routes.Route | None,
             sources: routecatalogs.Sources) -> tuple[routecatalogs.Figures | None, str]:
    """`(figures, source)` for one model: the route's catalog behind a gateway, else what the
    endpoint's own provider published, else — on Anthropic's own API alone — the static table.
    """
    if route is not None:
        return sources.route(route), ("table" if route.catalog == "anthropic" else route.catalog)
    if hit := table.get(mid):
        return hit, provider
    if provider == "table" and (static := _static_window(mid)) is not None:
        return (static, None), "table"
    return None, provider


def _row(figures: routecatalogs.Figures, name: str, source: str, route: routes.Route | None,
         sources: routecatalogs.Sources, now: datetime) -> dict:
    """One cache row. The output figure is the route's published maximum, except a placeholder
    (`routecatalogs.is_placeholder`), which is replaced by a real one or left unknown.
    """
    ctx, published = figures
    out = sources.output_for(name) if routecatalogs.is_placeholder(figures) else published
    return {"context_tokens": ctx, "max_output_tokens": out,
            "provider_max_output_tokens": published, "source": source,
            "route": route.label() if route is not None else None, "fetched": now.isoformat()}


# ------------------------------------------------------------------------------- the refresh ----

class _Refresh:
    """One refresh's working state: the catalogs read so far, each gateway's routing document,
    and the cache being written.
    """

    def __init__(self, server, cache: dict, now: datetime) -> None:
        self.server, self.cache, self.now = server, cache, now
        self.sources = routecatalogs.Sources(anthropic=_static_window)
        self.docs: dict[str, dict | None] = {}
        self.out: dict = {"fetched": now.isoformat(), "checked_models": [
            _key(mc.endpoint, mc.model) for mc in server.models.values()]}
        self.misses: list[str] = []
        cached_served = cache.get(SERVED_KEY)
        self.prev_served: dict = cached_served if isinstance(cached_served, dict) else {}
        self.served_out: dict[str, list[str]] = {}

    def _gateway(self, ep):
        """The endpoint's gateway and its routing document, or `(None, None)` without one."""
        gateway = (self.server.gateways or {}).get(ep.gateway) if ep.gateway else None
        if gateway is None:
            return None, None
        if gateway.name not in self.docs:
            self.docs[gateway.name] = routes.load(gateway)
        return gateway, self.docs[gateway.name]

    def endpoint(self, ep_name: str, ep, model_ids: list[str]) -> None:
        provider = catalogs.provider_of(ep)
        try:
            table, served = catalogs.read(provider, ep, model_ids)
        except Exception as exc:
            log.warning("limits: %s discovery failed: %s", ep_name, exc)
            table, served = {}, None
        # an UNANSWERED listing keeps the previous set — a down provider is not "not served"
        if served:
            self.served_out[ep_name] = sorted(served)
        elif kept := self.prev_served.get(ep_name):
            self.served_out[ep_name] = kept
        gateway, doc = self._gateway(ep)
        for mid in model_ids:
            route = routes.resolve(gateway, doc, mid) if doc is not None else None
            hit, source = None, provider
            if gateway is None or route is not None:
                hit, source = _figures(table, mid, provider, route, self.sources)
            if hit is None:
                self.misses.append(f"{ep_name}/{mid}")
                # keep whatever we knew before rather than forgetting it on one bad fetch
                if prev := self.cache.get(_key(ep_name, mid)):
                    self.out[_key(ep_name, mid)] = prev
                continue
            name = route.catalog_id() if route is not None else mid
            self.out[_key(ep_name, mid)] = _row(hit, name, source, route, self.sources, self.now)


def refresh(server, *, force: bool = False) -> dict:
    """Re-ask every configured provider and rewrite the cache. Returns `{written, skipped,
    misses}`. A provider fault never raises: one that is down leaves the previous figures in
    place (only the cache write itself can fail, and the caller logs it).

    Called from the daemon tick behind the TTL, never from `resolve`; a newly added model makes
    the cache stale at once (`_missing_models`).
    """
    home = server.routines_home
    cache = load(home)
    if not force and not stale(server):
        return {"written": 0, "skipped": len(_rows(cache)), "misses": []}

    by_endpoint: dict[str, list[str]] = {}
    for mc in server.models.values():
        by_endpoint.setdefault(mc.endpoint, []).append(mc.model)
    run = _Refresh(server, cache, datetime.now(UTC))
    for ep_name, model_ids in sorted(by_endpoint.items()):
        if (ep := server.endpoints.get(ep_name)) is not None:
            run.endpoint(ep_name, ep, model_ids)
    out = run.out
    if run.served_out:
        out[SERVED_KEY] = run.served_out
    try:   # the prices ride the same daily read of the same catalog (endpoints/prices.py)
        prices.refresh(server, run.sources.openrouter_body())
    except Exception as exc:
        log.warning("limits: price refresh failed: %s", exc)
    cache_path(home).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cache_path(home), out)
    if run.misses:
        log.info("limits: %d model(s) without route figures: %s",
                 len(run.misses), ", ".join(run.misses))
    return {"written": len(_rows(out)), "skipped": 0, "misses": run.misses}


def stale(server) -> bool:
    """Is the cache older than the TTL (or absent), has a configured model never been priced
    (`prices.missing`), or has a gateway's routing changed since? The daemon's tick check, and
    `refresh`'s own.
    """
    cache = load(server.routines_home)
    if _missing_models(server, cache) or prices.missing(server):
        return True
    fetched = cache.get("fetched")
    if not fetched:
        return True
    try:
        at = datetime.fromisoformat(str(fetched))
    except ValueError:
        return True
    return at + TTL <= datetime.now(UTC) or _routes_changed(server, at)


def _routes_changed(server, since: datetime) -> bool:
    """A gateway routing file modified after `since` — an alias may now name another route."""
    for gateway in (server.gateways or {}).values():
        try:
            mtime = expand(gateway.routes_file).stat().st_mtime if gateway.routes_file else 0.0
        except OSError:
            continue
        if mtime and datetime.fromtimestamp(mtime, UTC) > since:
            return True
    return False


def _missing_models(server, cache: dict) -> bool:
    """A fresh global timestamp must not hide newly added endpoint/model pairs."""
    checked = cache.get("checked_models", cache)
    return any(_key(mc.endpoint, mc.model) not in checked for mc in server.models.values())
