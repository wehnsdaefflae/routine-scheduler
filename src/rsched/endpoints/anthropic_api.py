"""Anthropic Messages API adapter.

Schema enforcement via forced tool-use: one tool named "action" whose input_schema is the
requested schema, with tool_choice forcing it — long-supported and reliable. Without a
schema it is a plain messages call.

Prompt caching is always on: cache_control breakpoints on the tools block and the system
prompt (static per run) plus a moving breakpoint on the last message — each turn re-reads
the whole prefix at ~0.1x price instead of full price. The engine's message list is
append-only, which is exactly what prefix caching needs. Cache traffic is reported as
usage "cached_in" / "cache_write" (kept out of "in"). A 400 naming cache_control gets one
degraded retry without the markers.
"""

from __future__ import annotations

import json

import httpx

from ..config import EndpointConfig
from .base import (DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT, Completion, EndpointError,
                   Message, json_or_raise, key_from_env_file, split_system, with_retries)

API_VERSION = "2023-06-01"

_EFFORT_ERROR_HINTS = ("effort", "output_config")


def merge_consecutive(messages: list[Message]) -> list[Message]:
    """The Messages API requires alternating roles; the engine legitimately produces
    consecutive user messages (observation + injection, compaction digests). Merge them."""
    merged: list[Message] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append(dict(m))
    return merged


def _mark_tail(messages: list[Message]) -> list[Message]:
    """Moving cache breakpoint on the LAST message: each turn the lookup matches the
    previous turn's breakpoint (the prefix is append-only) and re-reads everything before
    it from cache; only the newest exchange is fresh input."""
    if not messages:
        return messages
    out = [dict(m) for m in messages]
    last = out[-1]
    if isinstance(last.get("content"), str):
        out[-1] = {"role": last["role"],
                   "content": [{"type": "text", "text": last["content"],
                                "cache_control": {"type": "ephemeral"}}]}
    return out


def _strip_cache_control(body: dict) -> dict:
    """Degraded request without any cache markers (for gateways that reject them)."""
    out = json.loads(json.dumps(body))

    def scrub(node):
        if isinstance(node, dict):
            node.pop("cache_control", None)
            for v in node.values():
                scrub(v)
        elif isinstance(node, list):
            for v in node:
                scrub(v)
    scrub(out)
    if isinstance(out.get("system"), list):   # collapse block-form system back to a string
        out["system"] = "\n\n".join(b.get("text", "") for b in out["system"])
    return out


class AnthropicEndpoint:
    """Anthropic Messages API adapter — METERED per-token billing. Schema enforcement via
    a single forced tool-use; effort via `output_config`, degraded on a 400 naming it."""

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = cfg.api_key
        self.key_env_file = cfg.key_env_file
        self.key_var = cfg.key_var
        self.context_chars = cfg.context_chars

    def _api_key(self) -> str:
        if self.api_key:                                  # inline key (UI-set) wins over a file
            return self.api_key
        from ..secrets import load_secrets                # then the central secrets store
        if self.key_var and (k := load_secrets().get(self.key_var)):
            return k
        key = key_from_env_file(self.key_env_file, self.key_var) if self.key_env_file else None
        if not key:
            raise EndpointError(
                f"{self.name}: no API key — paste one in Settings, or put "
                f"`{self.key_var}=...` into {self.key_env_file}", auth=True
            )
        return key

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT, session: str | None = None) -> Completion:
        system, rest = split_system(messages)
        body: dict = {
            "model": model,
            "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
            "messages": _mark_tail(merge_consecutive(rest)),
        }
        if system:
            # static per run → a cache breakpoint; block form is what cache_control needs
            body["system"] = [{"type": "text", "text": system,
                               "cache_control": {"type": "ephemeral"}}]
        if effort:
            # The role's effort maps to the Messages API `output_config.effort` knob
            # (low/medium/high/xhigh/max — controls thinking depth and token spend).
            # A model that rejects it gets a degraded retry below.
            body["output_config"] = {"effort": effort}
        if schema is not None:
            body["tools"] = [{
                "name": "action",
                "description": "Return the next action as structured data.",
                "input_schema": schema,
                "cache_control": {"type": "ephemeral"},   # static per run → a breakpoint
            }]
            body["tool_choice"] = {"type": "tool", "name": "action"}
        headers = {"x-api-key": self._api_key(), "anthropic-version": API_VERSION}

        def call() -> Completion:
            resp = self._post(body, headers, timeout)
            if resp.status_code == 400:
                low = resp.text.lower()
                degraded = dict(body)
                if "output_config" in degraded and any(h in low for h in _EFFORT_ERROR_HINTS):
                    degraded.pop("output_config")
                if "cache_control" in low:   # a proxy/old gateway that rejects caching
                    degraded = _strip_cache_control(degraded)
                if json.dumps(degraded, sort_keys=True) != json.dumps(body, sort_keys=True):
                    resp = self._post(degraded, headers, timeout)
            return self._parse(resp)

        return with_retries(call)

    def _post(self, body: dict, headers: dict, timeout: int) -> httpx.Response:
        try:
            return httpx.post(f"{self.base_url}/v1/messages", json=body,
                              headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise EndpointError(f"{self.name}: {exc}", retryable=True) from exc

    def _parse(self, resp: httpx.Response) -> Completion:
        if resp.status_code in (401, 403):
            raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", auth=True)
        if resp.status_code in (429, 529) or resp.status_code >= 500:
            raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}", retryable=True)
        if resp.status_code != 200:
            raise EndpointError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
        data = json_or_raise(resp, self.name)
        parsed, texts = None, []
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "action":
                parsed = block.get("input")
            elif block.get("type") == "text":
                texts.append(block.get("text", ""))
        usage = data.get("usage") or {}
        out_usage = {"in": int(usage.get("input_tokens") or 0),
                     "out": int(usage.get("output_tokens") or 0)}
        # Cache traffic (input_tokens EXCLUDES it on this API): reads ~0.1x, writes ~1.25x.
        if usage.get("cache_read_input_tokens"):
            out_usage["cached_in"] = int(usage["cache_read_input_tokens"])
        if usage.get("cache_creation_input_tokens"):
            out_usage["cache_write"] = int(usage["cache_creation_input_tokens"])
        return Completion(
            text="\n".join(texts),
            parsed=parsed if isinstance(parsed, dict) else None,
            usage=out_usage,
        )
