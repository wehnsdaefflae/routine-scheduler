"""OpenAI-compatible chat-completions adapter — covers OpenRouter, Ollama, vLLM, OpenAI.

API key: inline `api_key` in config, or (preferred for real providers) `key_env_file` +
`key_var` pointing into ~/.credentials/. Schema enforcement via `response_format` in the
endpoint's configured mode:
  json_schema  → {"type":"json_schema","json_schema":{name,schema,strict}} (OpenRouter, OpenAI)
  json_object  → {"type":"json_object"} (guard validates)
  none         → nothing requested; the code-level validator + retry loop does all the work
A provider/model that rejects the requested response_format is retried once without it —
whether it says so with an HTTP 400 naming the field, or hides a schema-incapable backend
behind a generic 503 (some NanoGPT community backends). The schema guard downstream still
validates every reply.

Prompt caching is mostly implicit on this wire: OpenAI-, DeepSeek- and GLM-style backends
hit on a byte-stable prefix by themselves, and the hit comes back as
`prompt_tokens_details.cached_tokens`. Anthropic models served through an aggregator are
the exception — they cache only where a breakpoint says to — so a conversation turn on one
carries a top-level `cache_control` (OpenRouter's automatic last-block mode) and its
`cache_write_tokens` are folded into usage. A ONE-SHOT call (`cacheable=False`) places no
marker: its prefix is never sent again, so the write would buy a read that never comes.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from ..config import EndpointConfig
from .base import (
    DEFAULT_TIMEOUT,
    PDF_MIME,
    Completion,
    EndpointError,
    Message,
    json_or_raise,
    post_json,
    raise_for_status,
    read_media_b64,
    resolve_api_key,
    supports_media_type,
    with_retries,
)

log = logging.getLogger("rsched.endpoints.openai")

_RF_ERROR_HINTS = ("response_format", "json_schema", "structured")

# F362: a credit-metered provider (OpenRouter) answers 402 when the REQUESTED max_tokens
# costs more than the remaining balance, and the message names the number that would fit:
#   "You requested up to 16384 tokens, but can only afford 9590"
# The run is not out of credit — it asked for a ceiling it cannot pay for, which recurs as
# any balance drains no matter how often it is topped up. One degraded retry at the stated
# affordable ceiling turns a hard run-killing failure into a shorter completion.
_AFFORD_RE = re.compile(r"can only afford\s+(\d+)", re.IGNORECASE)
# Below this a retry is pointless — the reply would be too short to carry an action.
_MIN_AFFORDABLE_TOKENS = 600

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

#: Aggregator id prefixes whose upstream caches ONLY on an explicit breakpoint. Everything
#: else served over this wire (OpenAI, DeepSeek, the GLM/Qwen hosts) caches implicitly on a
#: byte-stable prefix, which the engine's append-only message list already gives them — so
#: the marker is sent to the models that need it and to no others.
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


def _openai_content(content: str, media: list[dict]) -> list[dict]:
    """A message's string content + media list → OpenAI content parts: text first, then each
    image as a base64 data-URI `image_url` part (the shape the `vision` util already uses).
    """
    parts: list[dict] = [{"type": "text", "text": content}] if content else []
    for item in media:
        mime = item["media_type"]
        try:
            # R1493: prefer bytes the engine captured when it verified the file — see the same
            # comment in anthropic_api._content_blocks. Re-reading the path on every send lets a
            # later overwrite or cleanup invalidate an attachment already promised to the model.
            b64 = item.get("b64") or read_media_b64(item["path"])
        except OSError as exc:
            parts.append({"type": "text", "text":
                          f"[Attachment unavailable: {item['path']}: {exc}. "
                          "The earlier observation remains in the conversation.]"})
            continue
        if mime == PDF_MIME:  # defensive — supports_media routes PDFs to the vision util
            parts.append({"type": "file", "file": {"filename": Path(item["path"]).name,
                                                    "file_data": f"data:{mime};base64,{b64}"}})
        else:
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return parts


def _render_media(messages: list[Message]) -> list[Message]:
    """Rewrite any message carrying `media` into OpenAI content-array form; text-only
    messages keep their plain string content. Drops the engine-side `media` key.
    """
    return [{"role": m["role"], "content": _openai_content(m.get("content", ""), m["media"])}
            if m.get("media") else {"role": m["role"], "content": m["content"]}
            for m in messages]


class OpenAICompatEndpoint:
    """Adapter for every OpenAI-compatible chat API — OpenRouter, Featherless, vLLM,
    Ollama, OpenAI itself. `extra_body` merges into each request (aggregator routing);
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
        # ollama_native: use Ollama's native /api/chat `format` field for REAL constrained
        # decoding to the schema (the OpenAI-compat response_format is not enforced by Ollama).
        self.native = cfg.schema_mode == "ollama_native"
        self.native_url = self.base_url.removesuffix("/v1") + "/api/chat"

    def supports_media(self, media_type: str, *, multimodal: bool) -> bool:
        """OpenAI-compatible vision models take images natively when the resolved model is
        multimodal; PDF support is spotty across providers, so PDFs route to the vision util.
        """
        return supports_media_type(media_type, multimodal=multimodal, pdf=False)

    def _resolve_key(self) -> str:
        # required=False: a keyless local backend (Ollama, vLLM) gets the "none" placeholder.
        return resolve_api_key(name=self.name, api_key=self.api_key, key_var=self.key_var,
                               key_env_file=self.key_env_file, required=False)

    def _response_format(self, schema: dict | None) -> dict | None:
        if schema is None or self.schema_mode == "none":
            return None
        if self.schema_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "action", "schema": schema, "strict": True},
        }

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 cacheable: bool = True,
                 temperature: float | None = None) -> Completion:
        # Nothing identifies the conversation: OpenAI-style providers cache implicitly on
        # byte-stable prefixes, which the engine's append-only message list already gives
        # them; the cached share shows up as usage "cached_in" (see _parse).
        temp = temperature if temperature is not None else self.temperature  # model wins
        if self.native and schema is not None:
            return self._complete_native(messages, model, schema, max_tokens, timeout, temp)
        if any(m.get("media") for m in messages):  # only touched when an image rides a turn
            messages = _render_media(messages)
        body: dict = {"model": model, "messages": messages, **self.extra_body}
        if "openrouter" in self.base_url:
            # usage accounting: the response's usage block then carries the real $ cost
            body.setdefault("usage", {"include": True})
            if cacheable and _needs_explicit_cache(model):
                # Anthropic models cache ONLY where a cache_control breakpoint says to —
                # implicit prefix caching is an OpenAI/DeepSeek behaviour, and routing the
                # same model through an aggregator does not add it. Without this the
                # metered Opus fallback re-sent its whole prefix at full price every turn:
                # birthday-admin 20260914-100002 turns 53-63, `in` ~100-117k each,
                # `cached_in` 0, $6.63 for twelve turns. OpenRouter's top-level field marks
                # the last cacheable block automatically, which is the same moving tail
                # breakpoint the direct adapter places. A provider that rejects the field
                # 400s naming it and gets one degraded retry without it (below).
                #
                # A request for caching, not a guarantee: this endpoint pins
                # `provider.order` with `allow_fallbacks: true`, and the measured run above
                # was served by a provider that was not first on that list. Read
                # `cache_read_share` on a metered run to know whether it took.
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
                # The provider priced the request out and TOLD US what fits. Retry once at
                # that ceiling instead of failing the turn: a shorter answer beats a dead
                # run, and the operator sees the squeeze in the endpoint log rather than as
                # a mystery 402. Below the floor there is no useful reply to be had, so the
                # 402 stands and failover (if configured) takes over.
                affordable = int(afford.group(1))
                if affordable >= _MIN_AFFORDABLE_TOKENS and affordable < (max_tokens or 0):
                    log.warning("%s: 402 priced out at max_tokens=%s; retrying at %s",
                                self.name, max_tokens, affordable)
                    alt = self._post({**body, "max_tokens": affordable}, headers, timeout)
                    if alt.status_code == 200:
                        resp = alt
            elif resp.status_code == 503 and "response_format" in body:
                # A backend that can't do schema-constrained decoding may reject
                # `response_format` with a 503 whose body never names the field —
                # indistinguishable by content from a real outage (NanoGPT's community model
                # backends do exactly this). Try ONCE without it; adopt the retry only if it
                # clears. A genuine outage still 503s and falls through to _parse's retryable
                # 5xx path, so with_retries backs off exactly as before.
                alt = self._post({k: v for k, v in body.items() if k != "response_format"},
                                 headers, timeout)
                if alt.status_code == 200:
                    resp = alt
            return self._parse(resp)

        return with_retries(call)

    def _complete_native(self, messages, model, schema, max_tokens, timeout,
                         temperature=None) -> Completion:
        """Ollama native /api/chat with `format` = the JSON schema → constrained decoding."""
        # num_ctx MUST be set: Ollama's default context is tiny, so a large prompt gets
        # silently truncated and schema enforcement degrades (the model emits stray keys).
        #
        # It is sized from the ENDPOINT's context_tokens, which is the only window this
        # adapter is given — `complete` receives the model's max_tokens but never its
        # resolved window. So an endpoint left at the 25,000 default in front of a 128k
        # model decodes at 25,000 however much compaction budgeted, and the prompt is
        # truncated silently. Set `context_tokens` on an Ollama ENDPOINT to its largest
        # served model until the resolved window is threaded through the protocol.
        options = {"num_ctx": max(8192, self.context_tokens)}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens:
            options["num_predict"] = max_tokens
        # Ollama's native chat takes images as a per-message base64 `images` list; the
        # engine-side `media` key must never ride the request (it holds local paths).
        native_msgs = []
        for message in messages:
            rendered = {"role": message["role"], "content": message.get("content", "")}
            images = []
            for item in message.get("media") or []:
                try:
                    images.append(item.get("b64") or read_media_b64(item["path"]))
                except OSError as exc:
                    rendered["content"] += (
                        f"\n[Attachment unavailable: {item['path']}: {exc}. "
                        "The earlier observation remains in the conversation.]")
            if images:
                rendered["images"] = images
            native_msgs.append(rendered)
        body = {"model": model, "messages": native_msgs, "format": schema, "stream": False,
                "options": options}

        def call() -> Completion:
            resp = post_json(self.native_url, body, None, timeout, name=self.name)
            raise_for_status(resp, self.name)
            data = json_or_raise(resp, self.name)
            text = (data.get("message") or {}).get("content", "") or ""
            return Completion(text=text,
                              usage={"in": int(data.get("prompt_eval_count") or 0),
                                     "out": int(data.get("eval_count") or 0)},
                              stop_reason=str(data.get("done_reason") or ""))

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
        # Implicit prompt caching (OpenAI/OpenRouter/DeepSeek-style): cached_tokens arrives
        # as a SUBSET of prompt_tokens on this API — subtract it so "in" is fresh input
        # only, the same cached-kept-OUT-of-"in" convention the other two adapters report
        # (token budgets keep their meaning; cache hit rates stay visible per run).
        details = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        # Writes are reported only by providers that cache EXPLICITLY (the Anthropic models
        # above). They come out of "in" exactly as reads already do — on THIS wire
        # `prompt_tokens` is the total and the details are its breakdown, so folding the key
        # without subtracting would double-count every written token into the run's token
        # budget, the opposite of the convention. Without the key at all,
        # `cache_read_share` returns None for the whole endpoint and `cache_read_degraded`
        # — the one signal separating a warm prefix from one re-written every turn — can
        # never fire on an aggregator path.
        written = int(details.get("cache_write_tokens") or 0)
        out: dict[str, int | float] = {
            "in": max(int(usage.get("prompt_tokens") or 0) - cached - written, 0),
            "out": int(usage.get("completion_tokens") or 0)}
        if cached:
            out["cached_in"] = cached
        if written:
            out["cache_write"] = written
        if usage.get("cost") is not None:   # OpenRouter usage accounting → $ (credits)
            out["cost"] = float(usage.get("cost") or 0)
        stop = str(data["choices"][0].get("finish_reason") or "")
        stop_details: dict = {}
        refusal = message.get("refusal")
        if not text.strip() and isinstance(refusal, str) and refusal.strip():
            # The spec's dedicated refusal field (structured-outputs declines): content is
            # null and finish_reason is often just "stop", so without this promotion the
            # decline would masquerade as an empty completion. The refusal prose rides
            # stop_details so the transcript shows WHY (R5). finish_reason
            # "content_filter" — the other openai-vocabulary decline — passes through
            # verbatim above; the engine treats both as refusal-shaped (REFUSAL_STOPS).
            stop = "refusal"
            stop_details = {"explanation": refusal.strip()}
        return Completion(text=text, usage=out, provider=str(data.get("provider") or ""),
                          stop_reason=stop, stop_details=stop_details)
