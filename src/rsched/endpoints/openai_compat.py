"""OpenAI-compatible chat-completions adapter — covers OpenRouter, Ollama, vLLM, OpenAI.

API key: inline `api_key` in config, or (preferred for real providers) `key_env_file` +
`key_var` pointing into ~/.credentials/. Schema enforcement via `response_format` in the
endpoint's configured mode:
  json_schema  → {"type":"json_schema","json_schema":{name,schema,strict}} (OpenRouter, Ollama ≥0.5, OpenAI)
  json_object  → {"type":"json_object"} (guard validates)
  none         → nothing requested; the code-level validator + retry loop does all the work
A provider/model that rejects the requested response_format is retried once without it —
whether it says so with an HTTP 400 naming the field, or hides a schema-incapable backend
behind a generic 503 (some NanoGPT community backends). The schema guard downstream still
validates every reply.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..config import EndpointConfig
from .base import (DEFAULT_TIMEOUT, PDF_MIME, Completion, EndpointError, Message,
                   json_or_raise, key_from_env_file, read_media_b64, supports_media_type,
                   with_retries)

_RF_ERROR_HINTS = ("response_format", "json_schema", "structured", "structured_outputs")


def _openai_content(content: str, media: list[dict]) -> list[dict]:
    """A message's string content + media list → OpenAI content parts: text first, then each
    image as a base64 data-URI `image_url` part (the shape the `vision` util already uses)."""
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
    messages keep their plain string content. Drops the engine-side `media` key."""
    return [{"role": m["role"], "content": _openai_content(m.get("content", ""), m["media"])}
            if m.get("media") else {"role": m["role"], "content": m["content"]}
            for m in messages]


class OpenAICompatEndpoint:
    """Adapter for every OpenAI-compatible chat API — OpenRouter, Featherless, vLLM,
    Ollama, OpenAI itself. `extra_body` merges into each request (aggregator routing);
    rejected `response_format`/`reasoning` fields get one degraded retry."""

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
        multimodal; PDF support is spotty across providers, so PDFs route to the vision util."""
        return supports_media_type(media_type, multimodal=multimodal, pdf=False)

    def _resolve_key(self) -> str:
        if self.api_key:                                  # inline key (UI-set) wins over a file
            return self.api_key
        from ..secrets import load_secrets                # then the central secrets store
        if self.key_var and (k := load_secrets().get(self.key_var)):
            return k
        if self.key_env_file:
            key = key_from_env_file(self.key_env_file, self.key_var)
            if not key:
                raise EndpointError(
                    f"{self.name}: no API key — paste one in Settings, or put "
                    f"`{self.key_var}=...` into {self.key_env_file}",
                    auth=True,
                )
            return key
        return "none"

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
                 timeout: int = DEFAULT_TIMEOUT, session: str | None = None,
                 temperature: float | None = None) -> Completion:
        # `session` is unused here: OpenAI-style providers cache implicitly on byte-stable
        # prefixes, which the engine's append-only message list already gives them; the
        # cached share shows up as usage "cached_in" (see _parse).
        temp = temperature if temperature is not None else self.temperature  # model wins, endpoint default
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
        body = {"model": model, "messages": messages, "format": schema, "stream": False,
                "options": options}

        def call() -> Completion:
            try:
                resp = httpx.post(self.native_url, json=body, timeout=timeout)
            except httpx.HTTPError as exc:
                raise EndpointError(f"{self.name}: {exc}", retryable=True) from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", retryable=True)
            if resp.status_code != 200:
                raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
            data = json_or_raise(resp, self.name)
            text = (data.get("message") or {}).get("content", "") or ""
            return Completion(text=text,
                              usage={"in": int(data.get("prompt_eval_count") or 0),
                                     "out": int(data.get("eval_count") or 0)})

        return with_retries(call)

    def _post(self, body: dict, headers: dict, timeout: int) -> httpx.Response:
        try:
            return httpx.post(f"{self.base_url}/chat/completions", json=body,
                              headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise EndpointError(f"{self.name}: {exc}", retryable=True) from exc

    def _parse(self, resp: httpx.Response) -> Completion:
        if resp.status_code in (401, 403):
            raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", auth=True)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", retryable=True)
        if resp.status_code != 200:
            raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
        data = json_or_raise(resp, self.name)
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise EndpointError(f"{self.name}: malformed response: {json.dumps(data)[:300]}") from exc
        if not text.strip():
            # Reasoning models sometimes spend the whole output budget "thinking" and leave
            # content empty; the answer (or at least the JSON) often sits in `reasoning`.
            text = message.get("reasoning") or ""
        usage = data.get("usage") or {}
        out = {"in": int(usage.get("prompt_tokens") or 0),
               "out": int(usage.get("completion_tokens") or 0)}
        # Implicit prompt caching (OpenAI/OpenRouter/DeepSeek-style): cached_tokens is the
        # SUBSET of prompt_tokens served from cache — surfaced so cache hit rates are
        # visible per run; "in" stays the full prompt count (this API's convention).
        details = usage.get("prompt_tokens_details") or {}
        if details.get("cached_tokens"):
            out["cached_in"] = int(details["cached_tokens"])
        if usage.get("cost") is not None:   # OpenRouter usage accounting → $ (credits)
            out["cost"] = float(usage.get("cost") or 0)
        return Completion(text=text, usage=out, provider=str(data.get("provider") or ""))
