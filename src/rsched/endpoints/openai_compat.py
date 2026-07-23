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
"""

from __future__ import annotations

import json
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

_RF_ERROR_HINTS = ("response_format", "json_schema", "structured")

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


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
        b64 = read_media_b64(item["path"])
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
        self.context_chars = cfg.context_chars
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
                 session: str | None = None,  # noqa: ARG002 — protocol caching hint (below)
                 temperature: float | None = None) -> Completion:
        # `session` is unused here: OpenAI-style providers cache implicitly on byte-stable
        # prefixes, which the engine's append-only message list already gives them; the
        # cached share shows up as usage "cached_in" (see _parse).
        temp = temperature if temperature is not None else self.temperature  # model wins
        if self.native and schema is not None:
            return self._complete_native(messages, model, schema, max_tokens, timeout, temp)
        if any(m.get("media") for m in messages):  # only touched when an image rides a turn
            messages = _render_media(messages)
        body: dict = {"model": model, "messages": messages, **self.extra_body}
        if "openrouter" in self.base_url:
            # usage accounting: the response's usage block then carries the real $ cost
            body.setdefault("usage", {"include": True})
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
                if degraded.keys() != body.keys():
                    resp = self._post(degraded, headers, timeout)
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
        # It uses the endpoint's context_chars default — the per-model window drives the
        # engine's compaction budget, not this local decode ceiling.
        options = {"num_ctx": max(8192, self.context_chars // 4)}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens:
            options["num_predict"] = max_tokens
        # Ollama's native chat takes images as a per-message base64 `images` list; the
        # engine-side `media` key must never ride the request (it holds local paths).
        native_msgs = [{"role": m["role"], "content": m.get("content", ""),
                        **({"images": [read_media_b64(i["path"]) for i in m["media"]]}
                           if m.get("media") else {})}
                       for m in messages]
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
        out: dict[str, int | float] = {
            "in": max(int(usage.get("prompt_tokens") or 0) - cached, 0),
            "out": int(usage.get("completion_tokens") or 0)}
        if cached:
            out["cached_in"] = cached
        if usage.get("cost") is not None:   # OpenRouter usage accounting → $ (credits)
            out["cost"] = float(usage.get("cost") or 0)
        return Completion(text=text, usage=out, provider=str(data.get("provider") or ""),
                          stop_reason=str(data["choices"][0].get("finish_reason") or ""))
