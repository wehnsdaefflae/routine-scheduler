"""Anthropic Messages API adapter.

Schema enforcement via tool use: one tool named "action" whose input_schema is the requested
schema, with tool_choice FORCING it (`_FORCED`). Forcing is what the subscription proxy serves
reliably: CLIProxyAPI strips `thinking` and `output_config.effort` from every forced call (the
Messages API allows thinking only on `auto`), so a forced call is answered there with the action
under its own name. 0.372.0 offered the tool on `auto` instead, to spare the newest models the
400 below; through the proxy that let a configured effort reach Opus, and its turns came back
`stop_reason: tool_use` with no action to read — every run failed over to its fallback model, so
the operator chose to force again (2026-10-01). Without a schema it is a plain messages call.

Every optional field a model may refuse — the forced `tool_choice`, `output_config` (effort),
`temperature`, the `cache_control` markers — rides the body, and a 400 that NAMES one degrades
it for a retry (`_degrade`). This adapter's KIND is a WIRE, not a provider: a subscription
proxy speaks it while serving `gpt-*` ids, Haiku 4.5 still honours temperature, and the newest
Claude models on the direct API (Fable 5.1, Opus 5.5, Sonnet 5.5) refuse forced tool use — on
those the tool is still offered, on `auto` with at most one call (`_ONE_CALL_AT_MOST`: the engine
takes ONE action per turn), and the engine reads an action from a text reply when the model
writes one instead. So no field is sent blindly or dropped blindly: the model that rejects one
says so, and pays a round trip per refused field.

A reply no action can be read from — no `action` call with an object input, and no text — says
what it DID carry: `stop_details["unread"]` lists its content blocks (each type, a tool call's
name and its input's type, never a value), and the engine's empty-completion error prints it.
The regression above was diagnosed blind because that error said only that nothing came back.

Prompt caching is on for CONVERSATIONS: cache_control breakpoints on the tools block and
the system prompt (static per run) plus a moving breakpoint on the last message — each turn
re-reads the whole prefix at ~0.1x price instead of full price. The engine's message list is
append-only, which is exactly what prefix caching needs. A ONE-SHOT call (`cacheable=False`,
derived from the task kind in instrument.CACHEABLE_KINDS) must not be written: its prefix is
never sent again, so a write would cost 1.25x for a read that never comes. It does NOT do
that by sending no marker — the subscription proxy places its own breakpoints on any request
that carries none — but by sending ONE on the smallest prefix it has: the tool definition, else
a 1,000-char leading slice of its first text (`_claim_placement`). Cache
traffic is reported as usage "cached_in" / "cache_write" (kept out of "in"). A 400 naming
cache_control gets one degraded retry without the markers.

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

#: Optional top-level fields and the words in a 400's body that name each. Current Claude
#: models REMOVED the sampling parameters and the newest refuse a forced tool_choice; the 400
#: is non-retryable, so without the degrade one filled Settings box — or the adapter's own
#: forced tool — failed a model over on every turn of every run, while the same wire still
#: serves models that accept the field. `cache_control` (nested) and `tool_choice` (replaced
#: before it is dropped) are handled in `_degrade` itself.
_DROPPABLE = (
    ("output_config", ("effort", "output_config")),
    ("temperature", ("temperature",)),
)

#: The tool_choice every schema'd call sends (see the module docstring for why it is forced).
_FORCED = {"type": "tool", "name": "action"}

#: What a refused forced tool_choice becomes: the API default, held to ONE call — `auto` alone
#: would let a reply carry several, and `_parse` keeps a single action.
_ONE_CALL_AT_MOST = {"type": "auto", "disable_parallel_tool_use": True}


def _usage(raw: dict) -> dict:
    """The Messages API's usage block → our usage dict. `input_tokens` EXCLUDES cache traffic
    on this API; cache reads/writes are surfaced as `cached_in` / `cache_write`, kept OUT of
    "in" so token budgets keep their meaning.
    """
    usage = {"in": int(raw.get("input_tokens") or 0),
             "out": int(raw.get("output_tokens") or 0)}
    if raw.get("cache_read_input_tokens"):
        usage["cached_in"] = int(raw["cache_read_input_tokens"])
    if raw.get("cache_creation_input_tokens"):
        usage["cache_write"] = int(raw["cache_creation_input_tokens"])
    return usage


def _describe(blocks: list[dict]) -> str:
    """What a reply no action could be read from carried, for the empty-completion error:
    each block's type, plus a tool call's name and the TYPE of its input — never a value, so
    nothing a model wrote or thought reaches the transcript through this line.
    """
    parts = []
    for block in blocks:
        kind = str(block.get("type") or "untyped")
        if kind == "tool_use":
            kind += (f" {str(block.get('name') or '')[:80]!r}"
                     f" (input: {type(block.get('input')).__name__})")
        parts.append(kind)
    return ", ".join(parts) or "no content blocks"


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
        try:
            # R1493: prefer bytes the engine captured when it verified the file. A message's
            # media rides the whole conversation, so re-reading the path here would re-read it
            # on every later send — and a run that overwrites or cleans up that file invalidates
            # an attachment the model was already told it would see. Only entries with no
            # captured bytes (conversation auto-attach) are read from disk.
            data = item.get("b64") or read_media_b64(item["path"])
        except OSError as exc:
            blocks.append({"type": "text", "text":
                           f"[Attachment unavailable: {item['path']}: {exc}. "
                           "The earlier observation remains in the conversation.]"})
            continue
        source = {"type": "base64", "media_type": mime, "data": data}
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


#: How much leading text a one-shot call's placement claim may cover: ~250 tokens, below the
#: minimum prefix any current Claude model caches at all (1,024 tokens at the smallest), so the
#: claim itself writes nothing.
CLAIM_SLICE_CHARS = 1_000

_EPHEMERAL = {"type": "ephemeral"}


def _claimed_text(text: str) -> list[dict]:
    """`text` as text blocks the claim marker can ride without covering it: the FIRST block at
    most CLAIM_SLICE_CHARS long and marked, the rest unmarked after it. The cut falls after the
    slice's last line break, else its last space, so the blocks join back into exactly `text`.
    A text that is short already, or one no cut leaves two non-blank halves of (the API refuses
    a whitespace-only text block), is one marked block — a short one is below the minimum anyway.
    """
    whole = [{"type": "text", "text": text, "cache_control": dict(_EPHEMERAL)}]
    if len(text) <= CLAIM_SLICE_CHARS:
        return whole
    window = text[:CLAIM_SLICE_CHARS]
    cut = window.rfind("\n") + 1 or window.rfind(" ") + 1 or CLAIM_SLICE_CHARS
    head, tail = text[:cut], text[cut:]
    if not head.strip() or not tail.strip():
        return whole
    return [{"type": "text", "text": head, "cache_control": dict(_EPHEMERAL)},
            {"type": "text", "text": tail}]


def _claim_placement(body: dict) -> dict:
    """A ONE-SHOT body with one cache marker on the SMALLEST prefix it has — the tool definition
    when there is one (tools lead the prefix, so nothing smaller exists), else a short leading
    slice of the system prompt, else of the first message (`_claimed_text`).

    No marker at all is not "no caching" on every wire. CLIProxyAPI, the subscription proxy
    every `anthropic` endpoint here runs through, adds its own breakpoints — on the tools, the
    system prompt and the last message — to any request that arrives carrying none
    (`shouldEnsureCacheControl`, v7.2.156; there is no setting for it). Measured over the week
    to 2026-10-08: 100% of four archival calls' input (2.94M tokens) and 95% of 190 `llm`
    subcalls' was billed as cache WRITES, at 1.25x, for prefixes nothing ever read again.
    One marker of the caller's own tells the proxy placement is taken, and caches at most the
    prefix up to it: a ~150-token tool definition for the archival call, a 1,000-char slice
    otherwise — both below the minimum prefix the API caches, so the claim writes nothing on
    the direct API either. Marking a whole long system prompt or a whole single-message prompt
    instead would write exactly what the claim exists to keep out. The one prefix with no text
    to slice is a message that opens with an image: that image is marked, and is at most one
    image's tokens.
    """
    if body.get("tools"):
        return {**body, "tools": [{**body["tools"][0], "cache_control": dict(_EPHEMERAL)},
                                  *body["tools"][1:]]}
    if body.get("system"):
        return {**body, "system": _claimed_text(body["system"])}
    messages = body.get("messages") or []
    if not messages:
        return body
    first = messages[0]
    content = first["content"]
    if isinstance(content, str):
        blocks = _claimed_text(content)
    elif content and content[0].get("type") == "text":
        blocks = [*_claimed_text(content[0]["text"]), *content[1:]]
    else:
        blocks = [{**content[0], "cache_control": dict(_EPHEMERAL)}, *content[1:]]
    return {**body, "messages": [{**first, "content": blocks}, *messages[1:]]}


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
        # joined with NOTHING: the only multi-block system is one `_claimed_text` cut out of a
        # single string, and any separator would insert text at the cut
        out["system"] = "".join(b.get("text", "") for b in out["system"])
    return out


def _degrade(body: dict, error: str) -> dict | None:
    """`body` with every optional field this 400 names degraded, or None when it names none
    that is still being sent — then the 400 stands. A forced tool_choice is first unforced
    (`_ONE_CALL_AT_MOST`) and dropped only if that is refused too; every other field is
    dropped. Each call changes at least one field and none comes back, so a caller looping on
    it ends after at most five degraded requests.
    """
    low = error.lower()
    out = {key: value for key, value in body.items()
           if not any(key == field and any(h in low for h in hints)
                      for field, hints in _DROPPABLE)}
    if "tool_choice" in low and "tool_choice" in out:
        if out["tool_choice"] == _ONE_CALL_AT_MOST:   # a gateway that does not know the field
            del out["tool_choice"]
        else:
            out["tool_choice"] = _ONE_CALL_AT_MOST
    if "cache_control" in low:   # a proxy/old gateway that rejects caching
        out = _strip_cache_control(out)
    return out if out != body else None


class AnthropicEndpoint:
    """Anthropic-compatible Messages adapter; billing belongs to the upstream. Schema via
    a single tool, forced where the model allows; effort via `output_config` and
    `temperature` when configured — each optional field degraded on a 400 naming it.
    """

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = cfg.api_key
        self.key_env_file = cfg.key_env_file
        self.key_var = cfg.key_var
        self.temperature = cfg.temperature

    def supports_media(self, media_type: str, *, multimodal: bool) -> bool:
        """The Messages API takes images AND PDFs (document blocks) natively when the resolved
        model is multimodal.
        """
        return supports_media_type(media_type, multimodal=multimodal, pdf=True)

    def _api_key(self) -> str:
        # A direct provider key or proxy client key is required; a miss raises auth-flagged.
        return resolve_api_key(name=self.name, api_key=self.api_key, key_var=self.key_var,
                               key_env_file=self.key_env_file, required=True)

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 temperature: float | None = None, cacheable: bool = True) -> Completion:
        system, rest = split_system(messages)
        rendered = _render_media(merge_consecutive(rest))
        body: dict = {
            "model": model,
            # the catalog's shared fallback — a call that passes no cap (a Settings
            # probe) gets the same 16_384 an unset catalog model resolves to
            "max_tokens": max_tokens or DEFAULT_MODEL_MAX_TOKENS,
            "messages": (_mark_tail(rendered) if cacheable else rendered),
        }
        temp = temperature if temperature is not None else self.temperature  # model wins
        if temp is not None:
            body["temperature"] = temp
        if system:
            # static per run → a cache breakpoint; block form is what cache_control needs
            body["system"] = ([{"type": "text", "text": system,
                                "cache_control": {"type": "ephemeral"}}] if cacheable
                              else system)
        if effort:
            # The role's effort maps to the Messages API `output_config.effort` knob
            # (low/medium/high/xhigh/max — controls thinking depth and token spend).
            # A model that rejects it gets a degraded retry below.
            body["output_config"] = {"effort": effort}
        if schema is not None:
            tool: dict = {"name": "action",
                          "description": "Return the next action as structured data.",
                          "input_schema": schema}
            if cacheable:
                tool["cache_control"] = {"type": "ephemeral"}   # static per run → a breakpoint
            body["tools"] = [tool]
            body["tool_choice"] = dict(_FORCED)
        if not cacheable:
            body = _claim_placement(body)
        headers = {"x-api-key": self._api_key(), "anthropic-version": API_VERSION}
        sent = body

        def call() -> Completion:
            # A 400 names ONE field and a model may refuse several (a current Claude model
            # refuses a forced tool_choice AND a sampling parameter), so degrade until the
            # 400 names nothing still sent. `sent` outlives the attempt: a retry after a
            # transient failure resends what is left instead of re-earning every 400.
            nonlocal sent
            resp = self._post(sent, headers, timeout)
            while resp.status_code == 400 and (smaller := _degrade(sent, resp.text)) is not None:
                sent = smaller
                resp = self._post(sent, headers, timeout)
            return self._parse(resp)

        return with_retries(call)

    def _post(self, body: dict, headers: dict, timeout: int) -> httpx.Response:
        return post_json(f"{self.base_url}/v1/messages", body, headers, timeout,
                         name=self.name)

    def _parse(self, resp: httpx.Response) -> Completion:
        raise_for_status(resp, self.name)
        data = json_or_raise(resp, self.name)
        blocks = data.get("content") or []
        parsed, texts = None, []
        for block in blocks:
            if block.get("type") == "tool_use" and block.get("name") == "action":
                parsed = block.get("input")
            elif block.get("type") == "text":
                texts.append(block.get("text", ""))
        parsed = parsed if isinstance(parsed, dict) else None
        stop = str(data.get("stop_reason") or "")
        # stop_details is populated by the API only on stop_reason "refusal" — a dict
        # like {"type": "refusal", "category": "cyber"|…|null, "explanation": …} — and is
        # null on every other stop; surfaced verbatim so the transcript can name WHY a
        # classifier declined (R5). A refusal is an HTTP 200, so it reaches this parse.
        raw = data.get("stop_details")
        details = raw if isinstance(raw, dict) else {}
        if parsed is None and not "".join(texts).strip() and stop != "refusal":
            details = {**details, "unread": _describe(blocks)}
        return Completion(
            text="\n".join(texts),
            parsed=parsed,
            usage=_usage(data.get("usage") or {}),  # reads ~0.1x, writes ~1.25x
            stop_reason=stop,
            stop_details=details,
        )
