"""What the PROVIDER says this model's limits are — discovered, not hand-entered.

Context capacity and output reservation are token counts end to end. Missing metadata
falls back to configured defaults; input occupancy is explicitly estimated by compaction.

## The two knobs need OPPOSITE treatment

This is the trap in "max out the tokens", and it is worth stating plainly:

- **The input window is adopted verbatim.** Pure win — a bigger window is more context.
- **The output cap is NOT maxed out.** Providers validate `input + requested_output <= window`
  up front (that is exactly what the live nano-gpt 400 above says), and
  `compaction.window_ceiling_tokens` subtracts `max_tokens` from the input budget for the same
  reason. Kimi K3's real 943,718-token output limit would collapse the usable prompt to ~10% of
  its 1M window. So the output cap resolves to `min(discovered, ENGINE_OUTPUT_CEILING)` — a
  ceiling on what THIS HARNESS needs for one JSON action plus reasoning, not a stand-in for what
  the model can do.

## Precedence, and why config still wins

`explicit config value` → `discovered` → `kind floor`. An operator who types a number is sizing
DOWN deliberately (a cost budget, a slow provider), and `engine/window.py` already promises to
honour that; discovery must not overrule it. What discovery replaces is the *absence* of a value,
which used to mean "a guess on the endpoint" and now means "ask the provider".

## Derived state, never config

The cache lives at `<routines_home>/.control/model-limits.json` — the pattern
`daemon/library_watch.py` sets for daemon-owned derived state, explicitly "never config". Nothing
here writes `config.yaml`: the web layer remains the only config writer, a run still writes no
config, and deleting this file costs one refresh. Resolution READS it and never fetches: `resolve`
is on the per-turn path and must not make a network call, so a miss is simply the floor.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..paths import atomic_write_json, read_json
from .base import CONNECT_TIMEOUT

log = logging.getLogger("rsched.limits")

LIMITS_FILE = Path(".control") / "model-limits.json"
#: How long a discovered figure is trusted before the daemon re-asks. Provider windows change on
#: the order of model releases, not hours.
TTL = timedelta(hours=24)
#: The most output tokens this harness ever needs in one completion: one JSON action, plus room
#: for a reasoning model to think and for an `llm` tool-call's answer. Deliberately NOT the
#: provider maximum — see the module docstring. 16k truncated `effort: max` turns; 32k has not.
ENGINE_OUTPUT_CEILING = 32_000
_TIMEOUT = 20

#: Claude windows for the ids no configured provider publishes figures FOR — a subscription
#: proxy whose `/v1/models` carries `{id, object, created, owned_by}` and nothing else, which is
#: every anthropic-kind endpoint here. (Anthropic's OWN listing has published
#: `max_input_tokens`/`max_tokens` since 2026-03; read it the day a direct endpoint is
#: configured.) A static table is a guess with a longer half-life than the guess it replaces, so
#: it is kept HERE beside the discovery code and its staleness is visible in Settings as
#: `source: table` rather than passing for a measurement.
STATIC_WINDOWS: dict[str, int] = {
    # https://platform.claude.com/docs/en/build-with-claude/context-windows (2026-09-10).
    # Specific revisions precede older families; unknown future revisions are not guessed.
    "claude-opus-4-6": 1_000_000, "claude-opus-4-7": 1_000_000,
    "claude-opus-4-8": 1_000_000, "claude-opus-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000, "claude-sonnet-5": 1_000_000,
    "claude-fable-5": 1_000_000, "claude-mythos-5": 1_000_000,
    "claude-opus-4-1": 200_000, "claude-opus-4-5": 200_000,
    "claude-sonnet-4-5": 200_000, "claude-haiku-4-5": 200_000,
    "claude-3": 200_000,
}

STATIC_OUTPUT = 32_000

#: What one provider's catalog route answered: the limits it published, keyed by model id, and
#: the ids it LISTS. The two are not the same set — a gateway may list a model and publish no
#: figures for it (CLIProxyAPI's `/v1/models` carries id/object/created/owned_by and nothing
#: else) — and conflating them is what made "no limit found" and "no such model" one message.
#: `None` for the id set means the question went UNANSWERED (no listing route, or it failed),
#: which is never the same as "the provider does not serve this".
Listing = tuple[dict[str, tuple[int, int | None]], set[str] | None]

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


# ------------------------------------------------------------------- per-provider discovery ----

def _provider(ep) -> str:
    """Which metadata API this endpoint speaks. Sniffed from base_url the way
    `endpoint_probe.credits_provider` already does, so the two read the same signals.

    The KIND is not that signal. An `anthropic` endpoint is Anthropic's own API only when it
    points at Anthropic's host; a subscription proxy speaks the same wire and serves whatever
    its upstreams do — OpenAI ids included — so it is sniffed like any other gateway and its
    `/models` route is read for whatever it carries. Claude ids are unaffected either way:
    `_static_window` is a fallback on a miss, not a property of the provider.
    """
    base = (ep.base_url or "").lower()
    if ep.kind == "anthropic" and ("api.anthropic.com" in base or not base):
        # Its listing DOES publish max_input_tokens/max_tokens (since 2026-03), but no
        # direct Anthropic endpoint is configured on this instance — both anthropic-kind
        # endpoints are subscription proxies, which are sniffed as gateways below. Read
        # the listing the day one is added; until then the table is the honest answer and
        # is labelled as one.
        return "table"
    if "openrouter" in base:
        return "openrouter"
    if "nano-gpt.com" in base:
        return "nanogpt"
    if ":11434" in base or "ollama" in base:
        return "ollama"
    return "openai"


def _models_url(ep) -> str:
    """The OpenAI-shaped catalog route for this endpoint. An `openai` base_url already carries
    the `/v1` (`…/api/v1`); an `anthropic` one deliberately does NOT, because the Messages
    adapter appends `/v1/messages` itself — so the listing lives one segment deeper there.
    Getting this wrong costs nothing visible: a 404 reads exactly like a gateway that publishes
    no catalog, and the miss would look like the model's own.
    """
    base = (ep.base_url or "").rstrip("/")
    return f"{base}/v1/models" if ep.kind == "anthropic" else f"{base}/models"


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _listed_ids(body: dict) -> set[str] | None:
    """Every id in an OpenAI-shaped `{"data": [{"id": …}]}` listing, whether or not the row
    carried any limits. None for a shape that is not that listing at all.
    """
    data = body.get("data")
    if not isinstance(data, list):
        return None
    return {str(r["id"]) for r in data if isinstance(r, dict) and r.get("id")}


def _get(url: str, headers: dict | None = None) -> dict | None:
    try:
        resp = httpx.get(url, headers=headers or {},
                         timeout=httpx.Timeout(_TIMEOUT, connect=CONNECT_TIMEOUT))
    except httpx.HTTPError as exc:
        log.info("limits: %s unreachable (%s)", url, exc)
        return None
    if resp.status_code != 200:
        log.info("limits: %s answered HTTP %s", url, resp.status_code)
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _openrouter(ep) -> Listing:
    """`GET {base_url}/models` → `context_length` + `top_provider.max_completion_tokens`.
    Public, needs no key. Ids are exact: `:free`, `:thinking` and `~`-prefixed variants are
    distinct entries, so a catalog id that is absent is a STALE CATALOG ENTRY, not a miss.
    """
    body = _get(_models_url(ep))
    if body is None:
        return {}, None
    out: dict[str, tuple[int, int | None]] = {}
    for row in body.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ctx = row.get("context_length")
        if not (isinstance(ctx, int | float) and ctx > 0):
            continue
        top = row.get("top_provider")
        mx = top.get("max_completion_tokens") if isinstance(top, dict) else None
        out[str(row["id"])] = (int(ctx),
                               int(mx) if isinstance(mx, int | float) and mx else None)
    return out, _listed_ids(body)


def _nanogpt(ep) -> Listing:
    """Nano-GPT publishes limits only on its OWN route — the OpenAI-compatible `/api/v1/models`
    carries none. Not a documented stable contract, so a shape change degrades to the floor.
    """
    body = _get(f"{_origin(ep.base_url or 'https://nano-gpt.com')}/api/models")
    if body is None:
        return {}, None
    models = body.get("models")
    text = models.get("text") if isinstance(models, dict) else None
    out: dict[str, tuple[int, int | None]] = {}
    for mid, row in (text or {}).items() if isinstance(text, dict) else []:
        if not isinstance(row, dict):
            continue
        ctx = row.get("maxInputTokens")
        if isinstance(ctx, int | float) and ctx > 0:
            mx = row.get("maxOutputTokens")
            out[str(mid)] = (int(ctx), int(mx) if isinstance(mx, int | float) and mx else None)
    served = {str(mid) for mid in text} if isinstance(text, dict) else None
    return out, served


def _openai_generic(ep) -> Listing:
    """The OpenAI spec's `/models` carries only id/object/created/owned_by — but vLLM adds
    `max_model_len` and several gateways add `context_length`. Opportunistic: a bare list is a
    miss, never a failure.
    """
    from .openai_compat import OpenAICompatEndpoint

    try:
        key = OpenAICompatEndpoint(ep)._resolve_key()
    except Exception:
        key = ""
    body = _get(_models_url(ep), {"Authorization": f"Bearer {key}"} if key else None)
    if body is None:
        return {}, None
    out: dict[str, tuple[int, int | None]] = {}
    for row in body.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ctx = row.get("max_model_len") or row.get("context_length")
        if isinstance(ctx, int | float) and ctx > 0:
            out[str(row["id"])] = (int(ctx), None)
    return out, _listed_ids(body)


def _ollama(ep, model_ids: list[str]) -> Listing:
    """`POST {origin}/api/show` per model → `model_info["<arch>.context_length"]`. Ollama has no
    output limit of its own, so the output cap is derived from the window rather than the floor.

    What this does NOT reach is `openai_compat`'s native `num_ctx`: `complete()` is never
    handed the resolved window, so the decode ceiling is still the ENDPOINT's
    `context_tokens`. Discovery therefore sizes compaction's budget correctly while the
    request itself can still be truncated — set an Ollama endpoint's `context_tokens` to
    its largest served model.
    """
    out: dict[str, tuple[int, int | None]] = {}
    for mid in model_ids:
        try:
            resp = httpx.post(
                f"{_origin(ep.base_url or '')}/api/show", json={"model": mid},
                timeout=httpx.Timeout(_TIMEOUT, connect=CONNECT_TIMEOUT))
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        try:
            info = (resp.json() or {}).get("model_info") or {}
        except ValueError:
            continue
        ctx = next((v for k, v in info.items()
                    if k.endswith(".context_length") and isinstance(v, int | float)), None)
        if ctx:
            out[mid] = (int(ctx), None)
    # No served set: /api/show is probed per id, so an id that did not answer is
    # indistinguishable from a daemon that was not running. Unanswered, never "absent".
    return out, None


# ------------------------------------------------------------------------------- the refresh ----

def refresh(server, *, force: bool = False) -> dict:
    """Re-ask every configured provider and rewrite the cache. Returns `{written, skipped,
    misses}`. Never raises: a provider that is down leaves the previous figures in place.

    Called from the daemon tick behind the TTL and from a Settings save, never from `resolve`.
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
        provider = _provider(ep)
        try:
            if provider == "openrouter":
                table, served = _openrouter(ep)
            elif provider == "nanogpt":
                table, served = _nanogpt(ep)
            elif provider == "ollama":
                table, served = _ollama(ep, model_ids)
            elif provider == "table":
                table, served = {}, None
            else:
                table, served = _openai_generic(ep)
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
                hit, provider_used = (static, STATIC_OUTPUT), "table"
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
                "max_output_tokens": min(max_out, ENGINE_OUTPUT_CEILING) if max_out
                                     else ENGINE_OUTPUT_CEILING,
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
