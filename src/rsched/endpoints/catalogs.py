"""What each provider's CATALOG says about its models — the per-provider readers behind
`limits.refresh`.

Three dialects, one answer shape (`Listing`): OpenRouter's public `/models` (sized by its
`top_provider`), Nano-GPT's detailed listing (`/api/v1/models?detailed=true`), and the
OpenAI-shaped `/models` every other gateway serves (vLLM adds
`max_model_len`; a subscription proxy lists ids and nothing else). A direct Anthropic endpoint is
read from nothing: `limits.STATIC_WINDOWS` answers for it. A GATEWAY endpoint's figures come from
the route each name takes instead (`routes`, `routecatalogs`); its own listing still says which
ids it serves. Every reader is opportunistic — a provider that is down, refuses, or answers in an
unknown shape reads as UNANSWERED, never as an error or "does not serve this model". What the
answers MEAN is `limits`' business.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

import httpx

from .base import CONNECT_TIMEOUT, resolve_api_key

log = logging.getLogger("rsched.limits")

_TIMEOUT = 20

#: What one provider's catalog route answered: the limits it published, keyed by model id, and
#: the ids it LISTS — not the same set (a gateway may list a model and publish no figures), so
#: "no limit found" and "no such model" stay distinct. `None` for the id set means UNANSWERED,
#: never "the provider does not serve this".
Listing = tuple[dict[str, tuple[int, int | None]], set[str] | None]


def provider_of(ep) -> str:
    """Which metadata API this endpoint speaks. Sniffed from base_url the way
    `endpoint_probe.credits_provider` already does, so the two read the same signals.

    The KIND is not that signal: an `anthropic` endpoint is Anthropic's own API only on
    Anthropic's host; a subscription proxy speaks the same wire and is sniffed like any gateway.
    The static Claude table answers for Anthropic's own API alone — a proxy may serve a Claude id
    on a smaller window.
    """
    base = (ep.base_url or "").lower()
    if ep.kind == "anthropic" and ("api.anthropic.com" in base or not base):
        # Its listing DOES publish max_input_tokens/max_tokens, but no direct Anthropic
        # endpoint is configured here; read it the day one is added. Until then, the table.
        return "table"
    if "openrouter" in base:
        return "openrouter"
    if "nano-gpt.com" in base:
        return "nanogpt"
    return "openai"


def read(provider: str, ep) -> Listing:
    """`provider`'s answer (as `provider_of` named it) for the models `ep` serves."""
    if provider == "openrouter":
        return _openrouter(ep)
    if provider == "nanogpt":
        return _nanogpt(ep)
    if provider == "table":
        return {}, None
    return _openai_generic(ep)


def models_url(ep) -> str:
    """The OpenAI-shaped catalog route for this endpoint. An `openai` base_url already carries
    the `/v1` (`…/api/v1`); an `anthropic` one deliberately does NOT, because the Messages
    adapter appends `/v1/messages` itself — so the listing lives one segment deeper there. A
    wrong route fails silently (a 404 reads like a gateway with no catalog).
    """
    base = (ep.base_url or "").rstrip("/")
    return f"{base}/v1/models" if ep.kind == "anthropic" else f"{base}/models"


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def listed_ids(body: dict) -> set[str] | None:
    """Every id in an OpenAI-shaped `{"data": [{"id": …}]}` listing, whether or not the row
    carried any limits. None for a shape that is not that listing at all.
    """
    data = body.get("data")
    if not isinstance(data, list):
        return None
    return {str(r["id"]) for r in data if isinstance(r, dict) and r.get("id")}


def get_json(url: str, headers: dict | None = None) -> dict | None:
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


def _positive(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and value > 0 else None


def openrouter_figures(row: dict) -> tuple[int, int | None] | None:
    """One OpenRouter row's ROUTE figures: `top_provider`'s window and output maximum — the
    provider OpenRouter actually routes to, whose window can sit below the row's headline — and
    the row's own window only where the top provider states none.
    """
    top = row.get("top_provider")
    top = top if isinstance(top, dict) else {}
    ctx = _positive(top.get("context_length")) or _positive(row.get("context_length"))
    return (ctx, _positive(top.get("max_completion_tokens"))) if ctx else None


def _openrouter(ep) -> Listing:
    """`GET {base_url}/models` → `openrouter_figures` per row. Public, needs no key. Ids are
    exact: `:free`, `:thinking` and `~`-prefixed variants are distinct entries, so a catalog id
    that is absent is a STALE CATALOG ENTRY, not a miss.
    """
    body = get_json(models_url(ep))
    if body is None:
        return {}, None
    out: dict[str, tuple[int, int | None]] = {}
    for row in body.get("data") or []:
        if isinstance(row, dict) and row.get("id") and (fig := openrouter_figures(row)):
            out[str(row["id"])] = fig
    return out, listed_ids(body)


#: OpenRouter's public listing: the PRICE source for every model (`prices.refresh`) and the
#: catalog of an OpenRouter ROUTE behind a gateway (`routecatalogs`). Needs no key.
REFERENCE_URL = "https://openrouter.ai/api/v1/models"
#: A trailing thinking-level on an id (`gemini-4-argon-high`, `gpt-oss-120b-medium`): a subscription
#: route serves one model as several ids that differ only in the level baked into them, and the
#: window belongs to the model, not to the level.
_LEVEL_SUFFIX = re.compile(r"-(?:minimal|low|medium|high|xhigh|max|thinking)$")
#: An Otari gateway's name for the PAY-PER-USE route of a model it also reaches on a
#: subscription (`claude-opus-5-metered` ↔ `claude-opus-5`, served by OpenRouter): the route is
#: the gateway's, the model — its list price, its window — is the same one.
_METERED_SUFFIX = re.compile(r"-metered$")


def reference_name(model_id: str) -> str:
    """A model id reduced to the name vendors share: the last path segment (`google/gemini-4-
    argon` → `gemini-4-argon`), lower-cased, any `:variant` (`:free`, `:batch`), the gateway's
    trailing `-metered` and then one trailing thinking level removed — so a proxy's
    `gemini-4-argon-high`, a gateway's `claude-opus-5-metered` and OpenRouter's
    `google/gemini-4-argon` / `anthropic/claude-opus-5` are the same names.
    """
    name = model_id.lower().rsplit("/", 1)[-1].split(":", 1)[0]
    return _LEVEL_SUFFIX.sub("", _METERED_SUFFIX.sub("", name))


def fetch_reference() -> dict | None:
    """The public reference catalog's whole listing, or None when it is unreachable. Two readers
    take different columns from it: `routecatalogs` an OpenRouter route's limits, `prices.refresh`
    the prices.
    """
    return get_json(REFERENCE_URL)


NANOGPT_ORIGIN = "https://nano-gpt.com"


def nanogpt_detailed(origin: str = NANOGPT_ORIGIN) -> Listing:
    """Nano-GPT's detailed listing (`/api/v1/models?detailed=true` — public, no key): the
    OpenAI-shaped route plus `context_length`/`max_output_tokens` per row. The plain route
    carries no figures, which is why the detailed one is asked.
    """
    body = get_json(f"{origin}/api/v1/models?detailed=true")
    if body is None or not isinstance(body.get("data"), list):
        return {}, None
    out: dict[str, tuple[int, int | None]] = {}
    for row in body["data"]:
        ctx = _positive(row.get("context_length")) if isinstance(row, dict) else None
        if ctx and row.get("id"):
            out[str(row["id"])] = (ctx, _positive(row.get("max_output_tokens")))
    return out, listed_ids(body)


def _nanogpt(ep) -> Listing:
    return nanogpt_detailed(_origin(ep.base_url or NANOGPT_ORIGIN))


def nanogpt_models(ep) -> dict[str, dict]:
    """Nano-GPT's own `/api/models` text-model rows by id — its PRICES (`prices.refresh`), or {}
    when it did not answer in the shape it publishes.
    """
    body = get_json(f"{_origin(ep.base_url or NANOGPT_ORIGIN)}/api/models")
    models = body.get("models") if isinstance(body, dict) else None
    text = models.get("text") if isinstance(models, dict) else None
    return {str(k): v for k, v in text.items() if isinstance(v, dict)} \
        if isinstance(text, dict) else {}


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
    body = get_json(models_url(ep), {"Authorization": f"Bearer {key}"} if key else None)
    if body is None:
        return {}, None
    out: dict[str, tuple[int, int | None]] = {}
    for row in body.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ctx = row.get("max_model_len") or row.get("context_length")
        if isinstance(ctx, int | float) and ctx > 0:
            out[str(row["id"])] = (int(ctx), None)
    return out, listed_ids(body)
