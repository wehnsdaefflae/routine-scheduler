"""OpenAI-compatible chat-completions adapter — covers OpenRouter, vLLM, OpenAI, gateways.

API key: inline `api_key` in config, or (preferred for real providers) `key_env_file` +
`key_var` pointing into ~/.credentials/. Schema enforcement via `response_format` in the
endpoint's configured mode:
  json_schema  → {"type":"json_schema","json_schema":{name,schema,strict}} (OpenRouter, OpenAI)
  none         → nothing requested; the code-level validator + retry loop does all the work
A provider/model that rejects the requested response_format (a 400 naming the field, or a
generic 503 from a schema-incapable backend) is retried once without it; the schema guard
downstream still validates every reply.

Prompt caching is mostly implicit on this wire (byte-stable prefix, reported as
`prompt_tokens_details.cached_tokens`). Anthropic models via an aggregator cache only on a
breakpoint, so a conversation turn on one carries a top-level `cache_control` and its
`cache_write_tokens` are folded into usage. A ONE-SHOT call (`cacheable=False`) places no
marker: its prefix is never sent again.

A bill the provider REPORTS — OpenRouter's usage accounting `cost`, else an Otari gateway's
`cost_usd` — becomes the usage's `cost` (`usage.reported_cost`).
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import EndpointConfig
from .base import (
    DEFAULT_TIMEOUT,
    Completion,
    EndpointError,
    Message,
    json_or_raise,
    post_json,
    raise_for_status,
    resolve_api_key,
    with_retries,
)
from .media_types import supports_media_type
from .openai_media import render_media
from .usage import reported_cost

log = logging.getLogger("rsched.endpoints.openai")

# A 400 naming the structured-output field earns one retry without it. Otari rewords a provider's
# refusal as "…does not support the requested response format." — the field's name with a space.
_RF_ERROR_HINTS = ("response_format", "response format", "json_schema", "structured")

# A credit-metered provider (OpenRouter) answers 402 when the REQUESTED max_tokens costs more
# than the balance, naming what would fit ("…but can only afford 9590"). One degraded retry at
# that ceiling turns a run-killing failure into a shorter completion (F362).
_AFFORD_RE = re.compile(r"can only afford\s+(\d+)", re.IGNORECASE)
# Below this a retry is pointless — the reply would be too short to carry an action.
_MIN_AFFORDABLE_TOKENS = 600

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

#: What THIS WIRE carries, independent of any model (D192): `input_audio` exists, no agreed
#: video part does. ANDed with the per-model bit in `supports_media`.
WIRE_AUDIO = True
WIRE_VIDEO = False

#: Aggregator id prefixes whose upstream caches ONLY on an explicit breakpoint; everything
#: else on this wire caches implicitly on a byte-stable prefix.
_EXPLICIT_CACHE_PREFIXES = ("anthropic/",)


def _needs_explicit_cache(model: str) -> bool:
    return model.lstrip("~").startswith(_EXPLICIT_CACHE_PREFIXES)


def _strip_think(text: str) -> str:
    """Hybrid-thinking models (qwen3, GLM, R1 distills) inline their scratchpad as a
    `<think>…</think>` preamble in `content` on many providers — the reasoning knob does
    not suppress it. The engine needs the ANSWER, so closed think blocks are dropped. An
    UNCLOSED `<think>` (output cap hit mid-thought) leaves the text untouched: better a
    visible schema retry than silently deleting the only content the model produced.
    """
    if "<think>" not in text:
        return text
    return _THINK_RE.sub("", text).lstrip()


class OpenAICompatEndpoint:
    """Adapter for every OpenAI-compatible chat API — OpenRouter, Featherless, vLLM, an Otari
    gateway, OpenAI itself. `extra_body` merges into each request (aggregator routing);
    rejected `response_format`/`reasoning` fields get one degraded retry.
    """

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = cfg.base_url.rstrip("/")
        self.api_key = cfg.api_key
        self.key_env_file = cfg.key_env_file
        self.key_var = cfg.key_var
        self.schema_mode = cfg.schema_mode
        self.context_tokens = cfg.context_tokens
        self.temperature = cfg.temperature
        self.extra_body = dict(cfg.extra_body)

    def supports_media(self, media_type: str, *, multimodal: bool, audio: bool,
                       video: bool) -> bool:
        """OpenAI-compatible vision models take images natively when the resolved model is
        multimodal; PDF support is spotty across providers, so PDFs route to the vision util.

        AUDIO rides this wire as `input_audio`, gated by the resolved MODEL's `audio` bit;
        VIDEO never does (no agreed video part), so the file keeps its fallback (D192).
        """
        return supports_media_type(media_type, multimodal=multimodal, pdf=False,
                                   audio=audio and WIRE_AUDIO, video=video and WIRE_VIDEO)

    def _resolve_key(self) -> str:
        # required=False: a keyless local backend (vLLM) gets the "none" placeholder.
        return resolve_api_key(name=self.name, api_key=self.api_key, key_var=self.key_var,
                               key_env_file=self.key_env_file, required=False)

    def _response_format(self, schema: dict | None) -> dict | None:
        if schema is None or self.schema_mode == "none":
            return None
        return {
            "type": "json_schema",
            "json_schema": {"name": "action", "schema": schema, "strict": True},
        }

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 cacheable: bool = True,
                 temperature: float | None = None) -> Completion:
        temp = temperature if temperature is not None else self.temperature  # model wins
        if any(m.get("media") for m in messages):  # only touched when an image rides a turn
            messages = render_media(messages)
        body: dict = {"model": model, "messages": messages, **self.extra_body}
        if "openrouter" in self.base_url:
            # usage accounting: the response's usage block then carries the real $ cost
            body.setdefault("usage", {"include": True})
            if cacheable and _needs_explicit_cache(model):
                # Anthropic models cache ONLY where a cache_control breakpoint says to, and
                # an aggregator does not add implicit caching. OpenRouter's top-level field
                # marks the last cacheable block (the same moving tail breakpoint the direct
                # adapter places); a provider that rejects it gets one retry without it.
                # A request, not a guarantee — read `cache_read_share` to know it took.
                body["cache_control"] = {"type": "ephemeral"}
        if temp is not None:
            body["temperature"] = temp
        if max_tokens:
            body["max_tokens"] = max_tokens
        if effort:
            # Reasoning models can burn the whole output budget "thinking" (truncated or
            # empty content). The role's effort maps to the reasoning knob (OpenRouter/
            # OpenAI style); providers that reject it get a degraded retry below.
            body["reasoning"] = {"effort": "low" if effort == "low" else
                                 "high" if effort in ("high", "xhigh", "max") else "medium"}
        rf = self._response_format(schema)
        if rf:
            body["response_format"] = rf
        headers = {"Authorization": f"Bearer {self._resolve_key()}"}

        def call() -> Completion:
            resp = self._post(body, headers, timeout)
            if resp.status_code == 400:
                low = resp.text.lower()
                degraded = dict(body)
                if "response_format" in degraded and any(h in low for h in _RF_ERROR_HINTS):
                    degraded.pop("response_format")
                if "reasoning" in degraded and "reasoning" in low:
                    degraded.pop("reasoning")
                if "cache_control" in degraded and "cache_control" in low:
                    degraded.pop("cache_control")
                if degraded.keys() != body.keys():
                    resp = self._post(degraded, headers, timeout)
            elif resp.status_code == 402 and (afford := _AFFORD_RE.search(resp.text)):
                # Priced out, and told what fits: retry once at that ceiling. Below the
                # floor the 402 stands and failover (if configured) takes over.
                affordable = int(afford.group(1))
                if affordable >= _MIN_AFFORDABLE_TOKENS and affordable < (max_tokens or 0):
                    log.warning("%s: 402 priced out at max_tokens=%s; retrying at %s",
                                self.name, max_tokens, affordable)
                    alt = self._post({**body, "max_tokens": affordable}, headers, timeout)
                    if alt.status_code == 200:
                        resp = alt
            elif resp.status_code == 503 and "response_format" in body:
                # A schema-incapable backend may reject `response_format` with a 503 that
                # never names it. Try ONCE without it; adopt the retry only if it clears —
                # a genuine outage still 503s into _parse's retryable path.
                alt = self._post({k: v for k, v in body.items() if k != "response_format"},
                                 headers, timeout)
                if alt.status_code == 200:
                    resp = alt
            return self._parse(resp)

        return with_retries(call)

    def _post(self, body: dict, headers: dict, timeout: int) -> httpx.Response:
        return post_json(f"{self.base_url}/chat/completions", body, headers, timeout,
                         name=self.name)

    def _parse(self, resp: httpx.Response) -> Completion:
        raise_for_status(resp, self.name)
        data = json_or_raise(resp, self.name)
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise EndpointError(
                f"{self.name}: malformed response: {json.dumps(data)[:300]}") from exc
        text = _strip_think(text)
        if not text.strip():
            # Reasoning models sometimes spend the whole output budget "thinking" and leave
            # content empty; the answer (or at least the JSON) often sits in `reasoning`
            # (OpenRouter-style) or `reasoning_content` (DeepSeek/vLLM/SGLang-style —
            # the qwen3 / GLM thinking models NanoGPT serves).
            text = message.get("reasoning") or message.get("reasoning_content") or ""
        usage = data.get("usage") or {}
        # On this wire `prompt_tokens` is the TOTAL and cached/written tokens are its
        # breakdown: subtract both so "in" is fresh input only, the convention the anthropic
        # adapter reports. Reporting `cache_write` is what lets `cache_read_degraded` fire.
        details = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        written = int(details.get("cache_write_tokens") or 0)
        out: dict[str, int | float] = {
            "in": max(int(usage.get("prompt_tokens") or 0) - cached - written, 0),
            "out": int(usage.get("completion_tokens") or 0)}
        if cached:
            out["cached_in"] = cached
        if written:
            out["cache_write"] = written
        if (cost := reported_cost(usage)) is not None:   # OpenRouter's `cost`, Otari's `cost_usd`
            out["cost"] = cost
        stop = str(data["choices"][0].get("finish_reason") or "")
        stop_details: dict = {}
        refusal = message.get("refusal")
        if not text.strip() and isinstance(refusal, str) and refusal.strip():
            # The spec's refusal field: content is null and finish_reason often "stop", so
            # promote it or the decline reads as an empty completion; the prose rides
            # stop_details. "content_filter" passes through above (both in REFUSAL_STOPS).
            stop = "refusal"
            stop_details = {"explanation": refusal.strip()}
        return Completion(text=text, usage=out, provider=str(data.get("provider") or ""),
                          stop_reason=stop, stop_details=stop_details)
