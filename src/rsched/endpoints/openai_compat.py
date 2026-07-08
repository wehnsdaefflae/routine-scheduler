"""OpenAI-compatible chat-completions adapter — covers Ollama, vLLM, OpenAI, OpenRouter.

Schema enforcement via `response_format` in the endpoint's configured mode:
  json_schema  → {"type":"json_schema","json_schema":{name,schema,strict}} (Ollama ≥0.5, OpenAI)
  json_object  → {"type":"json_object"} (schema text lands in the prompt; guard validates)
  none         → nothing requested; the code-level validator + retry loop does all the work
"""

from __future__ import annotations

import json

import httpx

from ..config import EndpointConfig
from .base import DEFAULT_TIMEOUT, Completion, EndpointError, Message, with_retries


class OpenAICompatEndpoint:
    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = cfg.base_url.rstrip("/")
        self.api_key = cfg.api_key or "none"
        self.schema_mode = cfg.schema_mode
        self.context_chars = cfg.context_chars
        self.temperature = cfg.temperature
        self.supports_schema = cfg.schema_mode == "json_schema"

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
                 timeout: int = DEFAULT_TIMEOUT) -> Completion:
        body: dict = {"model": model, "messages": messages}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if max_tokens:
            body["max_tokens"] = max_tokens
        rf = self._response_format(schema)
        if rf:
            body["response_format"] = rf

        def call() -> Completion:
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                raise EndpointError(f"{self.name}: {exc}", retryable=True) from exc
            if resp.status_code in (401, 403):
                raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", auth=True)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", retryable=True)
            if resp.status_code != 200:
                raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise EndpointError(f"{self.name}: malformed response: {json.dumps(data)[:300]}") from exc
            usage = data.get("usage") or {}
            return Completion(
                text=text,
                usage={"in": int(usage.get("prompt_tokens") or 0),
                       "out": int(usage.get("completion_tokens") or 0)},
            )

        return with_retries(call)
