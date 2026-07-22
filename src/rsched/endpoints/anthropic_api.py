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

Multimodal: a message may carry a `media` list ([{path, media_type}]); this API takes
images and PDFs natively, so those files become base64 image/document content blocks. Image
blocks are cache-eligible like text, so a viewed image re-reads at cache-read weight too.
"""

from __future__ import annotations

import json

import httpx

from ..config import DEFAULT_MODEL_MAX_TOKENS, EndpointConfig
from .base import (
    DEFAULT_TIMEOUT,
    PDF_MIME,
    Completion,
    Message,
    anthropic_usage,
    json_or_raise,
    post_json,
    raise_for_status,
    read_media_b64,
    resolve_api_key,
    split_system,
    supports_media_type,
    with_retries,
)

API_VERSION = "2023-06-01"

_EFFORT_ERROR_HINTS = ("effort", "output_config")


def merge_consecutive(messages: list[Message]) -> list[Message]:
    """The Messages API requires alternating roles; the engine legitimately produces
    consecutive user messages (observation + injection, compaction digests). Merge them —
    concatenating text content and carrying any `media` forward.
    """
    merged: list[Message] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            prev = merged[-1]
            combined = {"role": m["role"], "content": prev["content"] + "\n\n" + m["content"]}
            media = (prev.get("media") or []) + (m.get("media") or [])
            if media:
                combined["media"] = media
            merged[-1] = combined
        else:
            merged.append(dict(m))
    return merged


def _content_blocks(content: str, media: list[dict]) -> list[dict]:
    """A message's string content + its media list → Anthropic content blocks (text first,
    then each file as a base64 image or document block).
    """
    blocks: list[dict] = [{"type": "text", "text": content}] if content else []
    for item in media:
        mime = item["media_type"]
        source = {"type": "base64", "media_type": mime, "data": read_media_b64(item["path"])}
        blocks.append({"type": "document" if mime == PDF_MIME else "image", "source": source})
    return blocks


def _render_media(messages: list[Message]) -> list[Message]:
    """Turn any message carrying `media` into block-form content; text-only messages keep
    their plain string content (cache-stable). Drops the engine-side `media` key.
    """
    out: list[Message] = []
    for m in messages:
        if m.get("media"):
            out.append({"role": m["role"],
                        "content": _content_blocks(m.get("content", ""), m["media"])})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _mark_tail(messages: list[Message]) -> list[Message]:
    """Moving cache breakpoint on the LAST message: each turn the lookup matches the
    previous turn's breakpoint (the prefix is append-only) and re-reads everything before
    it from cache; only the newest exchange is fresh input. Handles both a plain string tail
    and an already-rendered block list (a media message) — the breakpoint rides its last
    block either way.
    """
    if not messages:
        return messages
    out = [dict(m) for m in messages]
    last = out[-1]
    content = last.get("content")
    if isinstance(content, str):
        out[-1] = {"role": last["role"],
                   "content": [{"type": "text", "text": content,
                                "cache_control": {"type": "ephemeral"}}]}
    elif isinstance(content, list) and content:
        blocks = [dict(b) for b in content]
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        out[-1] = {"role": last["role"], "content": blocks}
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
    a single forced tool-use; effort via `output_config`, degraded on a 400 naming it.
    """

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = cfg.api_key
        self.key_env_file = cfg.key_env_file
        self.key_var = cfg.key_var
        self.context_chars = cfg.context_chars
        self.temperature = cfg.temperature

    def supports_media(self, media_type: str, *, multimodal: bool) -> bool:
        """The Messages API takes images AND PDFs (document blocks) natively when the resolved
        model is multimodal.
        """
        return supports_media_type(media_type, multimodal=multimodal, pdf=True)

    def _api_key(self) -> str:
        # required=True: metered API — there is no keyless mode, a miss raises auth-flagged.
        return resolve_api_key(name=self.name, api_key=self.api_key, key_var=self.key_var,
                               key_env_file=self.key_env_file, required=True)

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 session: str | None = None,  # noqa: ARG002 — protocol caching hint; the
                 # always-on cache_control breakpoints make a per-run key unnecessary here
                 temperature: float | None = None) -> Completion:
        system, rest = split_system(messages)
        body: dict = {
            "model": model,
            # the catalog's shared fallback — a call that passes no cap (a Settings
            # probe) gets the same 16_384 an unset catalog model resolves to
            "max_tokens": max_tokens or DEFAULT_MODEL_MAX_TOKENS,
            "messages": _mark_tail(_render_media(merge_consecutive(rest))),
        }
        temp = temperature if temperature is not None else self.temperature  # model wins
        if temp is not None:
            body["temperature"] = temp
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
        return post_json(f"{self.base_url}/v1/messages", body, headers, timeout,
                         name=self.name)

    def _parse(self, resp: httpx.Response) -> Completion:
        raise_for_status(resp, self.name)
        data = json_or_raise(resp, self.name)
        parsed, texts = None, []
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "action":
                parsed = block.get("input")
            elif block.get("type") == "text":
                texts.append(block.get("text", ""))
        return Completion(
            text="\n".join(texts),
            parsed=parsed if isinstance(parsed, dict) else None,
            usage=anthropic_usage(data.get("usage") or {}),  # reads ~0.1x, writes ~1.25x
            stop_reason=str(data.get("stop_reason") or ""),
        )
