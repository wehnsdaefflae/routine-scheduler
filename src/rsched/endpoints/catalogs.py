"""What each provider's CATALOG says about its models — the per-provider readers behind
`limits.refresh`.

Four dialects, one answer shape (`Listing`): OpenRouter's public `/models`, Nano-GPT's own
`/api/models`, Ollama's per-model `/api/show`, and the OpenAI-shaped `/models` every other
gateway serves (vLLM adds `max_model_len`; a subscription proxy lists ids and nothing else). A
direct Anthropic endpoint is read from nothing: `limits.STATIC_WINDOWS` answers for it. Every
reader is opportunistic — a provider that is down, refuses, or answers in a shape it does not
know reads as UNANSWERED, never as an error and never as "does not serve this model". What the
answers MEAN (the output-cap policy, the precedence chain, the cache) is `limits`' business.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from .base import CONNECT_TIMEOUT, resolve_api_key

log = logging.getLogger("rsched.limits")

_TIMEOUT = 20

#: What one provider's catalog route answered: the limits it published, keyed by model id, and
#: the ids it LISTS. The two are not the same set — a gateway may list a model and publish no
#: figures for it (CLIProxyAPI's `/v1/models` carries id/object/created/owned_by and nothing
#: else) — and conflating them is what made "no limit found" and "no such model" one message.
#: `None` for the id set means the question went UNANSWERED (no listing route, or it failed),
#: which is never the same as "the provider does not serve this".
Listing = tuple[dict[str, tuple[int, int | None]], set[str] | None]


def provider_of(ep) -> str:
    """Which metadata API this endpoint speaks. Sniffed from base_url the way
    `endpoint_probe.credits_provider` already does, so the two read the same signals.

    The KIND is not that signal. An `anthropic` endpoint is Anthropic's own API only when it
    points at Anthropic's host; a subscription proxy speaks the same wire and serves whatever
    its upstreams do — OpenAI ids included — so it is sniffed like any other gateway and its
    `/models` route is read for whatever it carries. Claude ids are unaffected either way:
    the static table is a fallback on a miss, not a property of the provider.
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


def read(provider: str, ep, model_ids: list[str]) -> Listing:
    """`provider`'s answer (as `provider_of` named it) for the models `ep` serves."""
    if provider == "openrouter":
        return _openrouter(ep)
    if provider == "nanogpt":
        return _nanogpt(ep)
    if provider == "ollama":
        return _ollama(ep, model_ids)
    if provider == "table":
        return {}, None
    return _openai_generic(ep)


def models_url(ep) -> str:
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
    body = _get(models_url(ep))
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
    try:   # the adapters' own ladder; a keyless local backend reads "none" like they do
        key = resolve_api_key(name=ep.name, api_key=ep.api_key, key_var=ep.key_var,
                              key_env_file=ep.key_env_file, required=False)
    except Exception:
        key = ""
    body = _get(models_url(ep), {"Authorization": f"Bearer {key}"} if key else None)
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
    output limit of its own, so the output cap is derived from the window (`limits`).

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
