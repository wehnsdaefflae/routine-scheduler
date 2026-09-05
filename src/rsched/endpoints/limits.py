"""What the PROVIDER says this model's limits are — discovered, not hand-entered.

Operator, 2026-09-05: "I don't think the models that are set up use all their available tokens.
can we make them max out their token context window without the user having to set it up?" They
were right, and by more than it looked. Limits were pure hand-entry — of the 17 catalog models on
the live instance, ONE set `context_chars` and NONE set `max_tokens` — so every model rode a
guess made on its endpoint. Measured against the providers' own metadata: the OpenRouter endpoint
declared a 50,000-token window against real windows of 256,000–1,310,720, i.e. the engine was
using **4.8%** of Kimi K3's context. The `claude` endpoint erred the other way, claiming a
500,000-token window no Claude model has.

The self-correcting mechanism that was supposed to catch this (F278, `engine/window.py`) is
reactive by construction — it only ever learns from a request that already failed — and on this
instance it had never fired: `.control/health-events.jsonl` held 0 `model_window_corrected` rows
across 412, while carrying 7 `run_failed` rows whose text literally reads `Max context tokens:
65536`.

## The two knobs need OPPOSITE treatment

This is the trap in "max out the tokens", and it is worth stating plainly:

- **The input window is adopted verbatim.** Pure win — a bigger window is more context.
- **The output cap is NOT maxed out.** Providers validate `input + requested_output <= window`
  up front (that is exactly what the live nano-gpt 400 above says), and
  `compaction.window_ceiling_chars` subtracts `max_tokens` from the input budget for the same
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

#: Kinds with no metadata channel at all, and the windows their models actually have. A static
#: table is a guess with a longer half-life than the guess it replaces, so it is kept HERE beside
#: the discovery code (one table, two kinds read it) and its staleness is visible in Settings as
#: `source: table` rather than passing for a measurement.
STATIC_WINDOWS: dict[str, int] = {
    # Claude, by the alias the CLI accepts and by api id prefix
    "opus": 200_000, "sonnet": 200_000, "haiku": 200_000, "fable": 200_000,
    "claude-opus": 200_000, "claude-sonnet": 200_000, "claude-haiku": 200_000,
    "claude-3": 200_000, "claude-4": 200_000, "claude-5": 200_000,
}
STATIC_OUTPUT = 32_000


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


def window_chars(row: dict) -> int:
    """A discovered TOKEN window as the CHAR figure the engine budgets in, or 0 for a miss.

    Providers report tokens; the engine's compaction math is in chars at ~4/token
    (`engine/compaction.CHARS_PER_TOKEN`). The conversion happens once, here, so no call site
    has to remember which unit it is holding.
    """
    from ..engine.compaction import CHARS_PER_TOKEN

    ctx = row.get("context_tokens")
    return int(ctx * CHARS_PER_TOKEN) if isinstance(ctx, int | float) and ctx > 0 else 0


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
    """
    if ep.kind == "claude-cli":
        return "table"
    if ep.kind == "anthropic":
        return "table"          # /v1/models carries no window; a table is the honest answer
    base = (ep.base_url or "").lower()
    if "openrouter" in base:
        return "openrouter"
    if "nano-gpt.com" in base:
        return "nanogpt"
    if ":11434" in base or "ollama" in base:
        return "ollama"
    return "openai"


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _get(url: str, headers: dict | None = None) -> dict | None:
    try:
        resp = httpx.get(url, headers=headers or {}, timeout=_TIMEOUT)
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


def _openrouter(ep) -> dict[str, tuple[int, int | None]]:
    """`GET {base_url}/models` → `context_length` + `top_provider.max_completion_tokens`.
    Public, needs no key. Ids are exact: `:free`, `:thinking` and `~`-prefixed variants are
    distinct entries, so a catalog id that is absent is a STALE CATALOG ENTRY, not a miss.
    """
    body = _get(f"{(ep.base_url or '').rstrip('/')}/models") or {}
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
    return out


def _nanogpt(ep) -> dict[str, tuple[int, int | None]]:
    """Nano-GPT publishes limits only on its OWN route — the OpenAI-compatible `/api/v1/models`
    carries none. Not a documented stable contract, so a shape change degrades to the floor.
    """
    body = _get(f"{_origin(ep.base_url or 'https://nano-gpt.com')}/api/models") or {}
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
    return out


def _openai_generic(ep) -> dict[str, tuple[int, int | None]]:
    """The OpenAI spec's `/models` carries only id/object/created/owned_by — but vLLM adds
    `max_model_len` and several gateways add `context_length`. Opportunistic: a bare list is a
    miss, never a failure.
    """
    from .openai_compat import OpenAICompatEndpoint

    try:
        key = OpenAICompatEndpoint(ep)._resolve_key()
    except Exception:
        key = ""
    body = _get(f"{(ep.base_url or '').rstrip('/')}/models",
                {"Authorization": f"Bearer {key}"} if key else None) or {}
    out: dict[str, tuple[int, int | None]] = {}
    for row in body.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ctx = row.get("max_model_len") or row.get("context_length")
        if isinstance(ctx, int | float) and ctx > 0:
            out[str(row["id"])] = (int(ctx), None)
    return out


def _ollama(ep, model_ids: list[str]) -> dict[str, tuple[int, int | None]]:
    """`POST {origin}/api/show` per model → `model_info["<arch>.context_length"]`. Ollama has no
    output limit of its own, so the output cap is derived from the window rather than the floor —
    this is also what fixes `openai_compat`'s `num_ctx`, which had been sized from the ENDPOINT
    guess and so silently truncated every local model.
    """
    out: dict[str, tuple[int, int | None]] = {}
    for mid in model_ids:
        try:
            resp = httpx.post(f"{_origin(ep.base_url or '')}/api/show",
                              json={"model": mid}, timeout=_TIMEOUT)
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
    return out


# ------------------------------------------------------------------------------- the refresh ----

def refresh(server, *, force: bool = False) -> dict:
    """Re-ask every configured provider and rewrite the cache. Returns `{written, skipped,
    misses}`. Never raises: a provider that is down leaves the previous figures in place.

    Called from the daemon tick behind the TTL and from a Settings save, never from `resolve`.
    """
    home = server.routines_home
    cache = load(home)
    now = datetime.now(UTC)
    if not force and cache.get("fetched"):
        try:
            if datetime.fromisoformat(str(cache["fetched"])) + TTL > now:
                return {"written": 0, "skipped": len(cache) - 1, "misses": []}
        except ValueError:
            pass

    by_endpoint: dict[str, list[str]] = {}
    for mc in server.models.values():
        by_endpoint.setdefault(mc.endpoint, []).append(mc.model)

    out: dict = {"fetched": now.isoformat()}
    misses: list[str] = []
    for ep_name, model_ids in sorted(by_endpoint.items()):
        ep = server.endpoints.get(ep_name)
        if ep is None:
            continue
        provider = _provider(ep)
        try:
            if provider == "openrouter":
                table = _openrouter(ep)
            elif provider == "nanogpt":
                table = _nanogpt(ep)
            elif provider == "ollama":
                table = _ollama(ep, model_ids)
            elif provider == "table":
                table = {}
            else:
                table = _openai_generic(ep)
        except Exception as exc:
            log.warning("limits: %s discovery failed: %s", ep_name, exc)
            table = {}
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
    cache_path(home).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cache_path(home), out)
    written = len(out) - 1
    if misses:
        log.info("limits: %d model(s) not listed by their provider: %s",
                 len(misses), ", ".join(misses))
    return {"written": written, "skipped": 0, "misses": misses}


def stale(server) -> bool:
    """Is the cache older than the TTL (or absent)? The daemon's tick check."""
    fetched = load(server.routines_home).get("fetched")
    if not fetched:
        return True
    try:
        return datetime.fromisoformat(str(fetched)) + TTL <= datetime.now(UTC)
    except ValueError:
        return True
