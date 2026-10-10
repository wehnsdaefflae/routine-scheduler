"""How the engine's message list becomes a Messages API request's content — role merging, media
blocks, NATIVE tool turns and cache markers. Pure functions of the list: the transport
(`anthropic_api.py`) decides which apply to a call and posts what they return.

**Native tool turns** (`native_turns`). The engine keeps every past action as an assistant
message holding its JSON and every observation as a user message — one provider-agnostic list.
Sent like that on the Anthropic wire with the action tool on `auto`, a thinking model reads its
own history as JSON TEXT and imitates it: it writes an action as text, which ends nothing, thinks
on as though it had run, and makes the call for the next — two actions in one reply, the second
narrating the first as done (R2443-R2445; engine/replyactions.py runs the first). So a schema'd
call on `auto` renders each past action as what it was, a call of the offered tool — `tool_use`
— and the message after it as that call's `tool_result`. A reply then ends at its one call, as
the API ends every tool turn. Replaying the real turn 194 of llmsectest-weekday:20261010-040001
through the live proxy: text history gave two actions in 5 replies of 5; native history gave ONE
call in 5 of 5, with thinking on and the whole prefix read from cache.

The API does not require the signed thinking block of an earlier reply to be passed back (measured
the same day: a continuation with and without it were both accepted), so nothing beyond the
engine's list is needed and nothing is stored. The rendering is deterministic and positional — a
call's id is its index in the merged list — so a message renders the same bytes on every later
turn and the cached prefix holds. A message that is not exactly one JSON object (schema-retry
debris cut at 4,000 characters, two merged replies) stays text, and so does the message after
it. A forced call (`tool_choice: forced`, the Codex route) keeps the text history it was
validated on: forcing already allows one call and nothing else.
"""

from __future__ import annotations

import json

from .base import PDF_MIME, Message, read_media_b64


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


def content_blocks(content: str, media: list[dict]) -> list[dict]:
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


def render_media(messages: list[Message]) -> list[Message]:
    """Turn any message carrying `media` into block-form content; text-only messages keep
    their plain string content (cache-stable). Drops the engine-side `media` key.
    """
    out: list[Message] = []
    for m in messages:
        if m.get("media"):
            out.append({"role": m["role"],
                        "content": content_blocks(m.get("content", ""), m["media"])})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _call_input(content: object) -> dict | None:
    """The tool input an assistant message IS — its whole text one JSON object — or None."""
    if not isinstance(content, str):
        return None
    try:
        obj = json.loads(content)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _result_of(call_id: str, content: object) -> list[dict]:
    """A user message as the result of `call_id`: its text inside the `tool_result`, any image or
    document blocks after it — the API wants every tool_result first in its message.
    """
    if isinstance(content, str):
        return [{"type": "tool_result", "tool_use_id": call_id, "content": content}]
    blocks = content if isinstance(content, list) else []
    text = [b for b in blocks if b.get("type") == "text"]
    result: dict = {"type": "tool_result", "tool_use_id": call_id}
    if text:
        result["content"] = text
    return [result, *(b for b in blocks if b.get("type") != "text")]


def native_turns(messages: list[Message], tool: str) -> list[Message]:
    """`messages` (merged, media rendered) with each past action as a call of `tool` and the
    message after it as that call's result — the module docstring says why. Every other
    message is returned as it was; a call with no message after it stays text, because the API
    refuses a call without its result.
    """
    out: list[Message] = []
    call_id: str | None = None
    for index, m in enumerate(messages):
        call = _call_input(m["content"]) if m["role"] == "assistant" else None
        if call is not None:
            call_id = f"rs_{index:05d}"
            out.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": tool, "input": call}]})
            continue
        if call_id is not None and m["role"] == "user":
            out.append({"role": "user", "content": _result_of(call_id, m["content"])})
        else:
            out.append(m)
        call_id = None
    if call_id is not None:
        out[-1] = messages[-1]
    return out


def mark_tail(messages: list[Message]) -> list[Message]:
    """Moving cache breakpoint on the LAST message: each turn the lookup matches the
    previous turn's breakpoint (the prefix is append-only) and re-reads everything before
    it from cache; only the newest exchange is fresh input. Handles both a plain string tail
    and an already-rendered block list (a media message, a tool result) — the breakpoint rides
    its last block either way.
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

EPHEMERAL = {"type": "ephemeral"}


def _claimed_text(text: str) -> list[dict]:
    """`text` as text blocks the claim marker can ride without covering it: the FIRST block at
    most CLAIM_SLICE_CHARS long and marked, the rest unmarked after it. The cut falls after the
    slice's last line break, else its last space, so the blocks join back into exactly `text`.
    A text that is short already, or one no cut leaves two non-blank halves of (the API refuses
    a whitespace-only text block), is one marked block — a short one is below the minimum anyway.
    """
    whole = [{"type": "text", "text": text, "cache_control": dict(EPHEMERAL)}]
    if len(text) <= CLAIM_SLICE_CHARS:
        return whole
    window = text[:CLAIM_SLICE_CHARS]
    cut = window.rfind("\n") + 1 or window.rfind(" ") + 1 or CLAIM_SLICE_CHARS
    head, tail = text[:cut], text[cut:]
    if not head.strip() or not tail.strip():
        return whole
    return [{"type": "text", "text": head, "cache_control": dict(EPHEMERAL)},
            {"type": "text", "text": tail}]


def claim_placement(body: dict) -> dict:
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
        return {**body, "tools": [{**body["tools"][0], "cache_control": dict(EPHEMERAL)},
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
        blocks = [{**content[0], "cache_control": dict(EPHEMERAL)}, *content[1:]]
    return {**body, "messages": [{**first, "content": blocks}, *messages[1:]]}


def strip_cache_control(body: dict) -> dict:
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
