"""Anthropic Messages API adapter.

Schema enforcement via tool use: one tool named "action" whose input_schema is the requested
schema, offered on plain `auto` (`_AUTO`), so a reply may chain CALLS. Without one it is a plain
messages call.

**Why not forced.** The Messages API allows thinking only on `auto`, so a forced `tool_choice`
makes a configured EFFORT meaningless (the subscription proxy strips it), and the newest Claude
models refuse forcing outright. An `auto` reply no action can be read from is RE-ASKED once,
forced (`_reask_forced`), inside the same call; the completion marks it in
`stop_details["forced_reask"]`. An endpoint validated on the forced route keeps it with
`tool_choice: forced` on its config.

Every optional field a model may refuse — the forced `tool_choice`, `output_config` (effort),
`temperature`, the `cache_control` markers — rides the body, and a 400 that NAMES one degrades
it for a retry (`_degrade`). This adapter's KIND is a WIRE, not a provider. On `auto` a reply may
carry SEVERAL calls: they are READ in reply order (`parsed` + `parsed_rest`) and
`engine/chain.py` runs them in that order.

**Past actions are sent as CALLS** (`anthropic_messages.native_turns`): a model reading its own
history as JSON text writes its next action as text too, which ends nothing. So each past action
renders as a `tool_use` of the action tool and the message after it as that call's
`tool_result`.

A reply no action can be read from says what it DID carry: `stop_details["unread"]` lists its
content blocks (types and a call's input type, never a value).

Prompt caching: cache_control breakpoints on the tools block and the system prompt plus a moving
breakpoint on the last message. A ONE-SHOT call (`cacheable=False`) must not write a cache it
will never read; since the proxy places its own breakpoints on a request carrying none, it sends
ONE on its smallest prefix (`anthropic_messages.claim_placement`). Cache traffic is reported as
usage "cached_in" / "cache_write" (kept out of "in"); a billed figure the wire reports as "cost".

Multimodal: images and PDFs in a message's `media` list become base64 content blocks. Every
message-level rendering lives in `anthropic_messages.py`; this module is the transport.
"""

from __future__ import annotations

import logging

import httpx

from ..config import DEFAULT_MODEL_MAX_TOKENS, EndpointConfig
from .anthropic_messages import (
    claim_placement,
    mark_tail,
    merge_consecutive,
    native_turns,
    render_media,
    strip_cache_control,
)
from .base import (
    DEFAULT_TIMEOUT,
    Completion,
    EndpointError,
    Message,
    json_or_raise,
    post_json,
    raise_for_status,
    resolve_api_key,
    split_system,
    with_retries,
)
from .media_types import supports_media_type
from .usage import reading as usage_reading
from .usage import summed

log = logging.getLogger("rsched.endpoints.anthropic")

API_VERSION = "2023-06-01"

#: What THIS WIRE carries, independent of any model (D192): no audio or video blocks exist.
WIRE_AUDIO = False
WIRE_VIDEO = False

#: Optional top-level fields and the words in a 400's body that name each (the 400 is
#: non-retryable, the same wire serves models that accept them). `cache_control` and
#: `tool_choice` are handled in `_degrade` itself.
_DROPPABLE = (
    ("output_config", ("effort", "output_config")),
    ("temperature", ("temperature",)),
)

#: The forced tool_choice: what an endpoint configured `tool_choice: forced` sends, and the
#: one re-ask of an unreadable `auto` reply.
_FORCED = {"type": "tool", "name": "action"}

#: The tool_choice every schema'd call sends by default: the API default, which lets a reply
#: carry SEVERAL calls (the engine chains them).
_AUTO = {"type": "auto"}


def _usage(raw: dict) -> dict:
    """The Messages API's usage block → our usage dict, via the one `usage.reading`.

    This wire's own fact, and all this function adds: `input_tokens` already EXCLUDES cache
    traffic here, so it IS the fresh input — unlike the OpenAI shape, where the prompt count is
    a total the adapter subtracts from. The shape of the result, the zero-is-absent rule and
    the reported bill are `endpoints/usage.py`'s, which owns that vocabulary.
    """
    return usage_reading(raw,
                         fresh_in=int(raw.get("input_tokens") or 0),
                         out=int(raw.get("output_tokens") or 0),
                         cached_in=int(raw.get("cache_read_input_tokens") or 0),
                         cache_write=int(raw.get("cache_creation_input_tokens") or 0))


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


def _degrade(body: dict, error: str) -> dict | None:
    """`body` with every optional field this 400 names degraded, or None when it names none
    that is still being sent — then the 400 stands. A forced tool_choice is first unforced
    (`_AUTO`) and dropped only if that is refused too; every other field is
    dropped. Each call changes at least one field and none comes back, so a caller looping on
    it ends after at most five degraded requests.
    """
    low = error.lower()
    out = {key: value for key, value in body.items()
           if not any(key == field and any(h in low for h in hints)
                      for field, hints in _DROPPABLE)}
    if "tool_choice" in low and "tool_choice" in out:
        if out["tool_choice"] == _AUTO:   # a gateway that does not know the field
            del out["tool_choice"]
        else:
            out["tool_choice"] = _AUTO
    if "cache_control" in low:   # a proxy/old gateway that rejects caching
        out = strip_cache_control(out)
    return out if out != body else None


class AnthropicEndpoint:
    """Anthropic-compatible Messages adapter; billing belongs to the upstream, which may report
    it (`_usage`). Schema via a single tool on `auto` (forced only where the endpoint says so);
    effort via `output_config` and `temperature` when configured — each optional field degraded
    on a 400 naming it.
    """

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = cfg.api_key
        self.key_env_file = cfg.key_env_file
        self.key_var = cfg.key_var
        self.temperature = cfg.temperature
        self.tool_choice = cfg.tool_choice

    def supports_media(self, media_type: str, *, multimodal: bool, audio: bool,
                       video: bool) -> bool:
        """The Messages API takes images AND PDFs (document blocks) natively when the resolved
        model is multimodal.

        It takes NEITHER audio nor video (D192), however the catalog model is configured.
        """
        return supports_media_type(media_type, multimodal=multimodal, pdf=True,
                                   audio=audio and WIRE_AUDIO, video=video and WIRE_VIDEO)

    def _api_key(self) -> str:
        # A direct provider key or proxy client key is required; a miss raises auth-flagged.
        return resolve_api_key(name=self.name, api_key=self.api_key, key_var=self.key_var,
                               key_env_file=self.key_env_file, required=True)

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 temperature: float | None = None, cacheable: bool = True) -> Completion:
        system, rest = split_system(messages)
        rendered = render_media(merge_consecutive(rest))
        if schema is not None and self.tool_choice != "forced":
            # past actions as calls of the offered tool, so a reply ends at its one call
            # (anthropic_messages.native_turns says why, and why not on a forced route)
            rendered = native_turns(rendered, "action")
        body: dict = {
            "model": model,
            # the catalog's shared fallback — a call that passes no cap (a Settings
            # probe) gets the same 16_384 an unset catalog model resolves to
            "max_tokens": max_tokens or DEFAULT_MODEL_MAX_TOKENS,
            "messages": (mark_tail(rendered) if cacheable else rendered),
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
            # the role's effort → `output_config.effort`; a refusal degrades below
            body["output_config"] = {"effort": effort}
        if schema is not None:
            tool: dict = {"name": "action",
                          "description": "Return the next action as structured data.",
                          "input_schema": schema}
            if cacheable:
                tool["cache_control"] = {"type": "ephemeral"}   # static per run → a breakpoint
            body["tools"] = [tool]
            body["tool_choice"] = dict(_FORCED if self.tool_choice == "forced"
                                       else _AUTO)
        if not cacheable:
            body = claim_placement(body)
        headers = {"x-api-key": self._api_key(), "anthropic-version": API_VERSION}
        sent = body

        def call() -> Completion:
            # A 400 names ONE field and a model may refuse several, so degrade until it names
            # nothing still sent. `sent` outlives the attempt, so a retry keeps the degrades.
            nonlocal sent
            resp = self._post(sent, headers, timeout)
            while resp.status_code == 400 and (smaller := _degrade(sent, resp.text)) is not None:
                sent = smaller
                resp = self._post(sent, headers, timeout)
            completion = self._parse(resp)
            if "unread" in completion.stop_details and sent.get("tool_choice") != _FORCED \
                    and "tools" in sent:
                return self._reask_forced(sent, headers, timeout, completion)
            return completion

        return with_retries(call)

    def _reask_forced(self, sent: dict, headers: dict, timeout: int,
                      unread: Completion) -> Completion:
        """An `auto` reply no action could be read from, asked ONCE more with the tool forced.
        The forced answer runs at the proxy's default effort, so it is marked; when the re-ask
        fails or is unreadable too, the first reply is returned as it was.
        """
        what = unread.stop_details["unread"]
        log.warning("%s: an auto reply carried no readable action (%s) — re-asking forced",
                    self.name, what)
        try:
            again = self._parse(self._post({**sent, "tool_choice": dict(_FORCED)}, headers,
                                           timeout))
        except EndpointError as exc:
            log.warning("%s: the forced re-ask failed too: %s", self.name, exc)
            return unread
        if again.parsed is None and not again.text.strip():
            return unread
        return Completion(text=again.text, parsed=again.parsed,
                          parsed_rest=again.parsed_rest,
                          usage=summed(unread.usage, again.usage),
                          stop_reason=again.stop_reason,
                          stop_details={**again.stop_details, "forced_reask": what})

    def _post(self, body: dict, headers: dict, timeout: int) -> httpx.Response:
        return post_json(f"{self.base_url}/v1/messages", body, headers, timeout,
                         name=self.name)

    def _parse(self, resp: httpx.Response) -> Completion:
        raise_for_status(resp, self.name)
        data = json_or_raise(resp, self.name)
        blocks = data.get("content") or []
        calls = [b.get("input") for b in blocks
                 if b.get("type") == "tool_use" and b.get("name") == "action"]
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        # Every readable call is kept — `parsed` the first, `parsed_rest` the rest — never
        # dropped, or the false-claim guard goes blind on this channel.
        objects = [c for c in calls if isinstance(c, dict)]
        parsed = objects[0] if calls and isinstance(calls[0], dict) else None
        rest = objects[1:] if parsed is not None else []
        if rest:
            log.info("%s: a reply chained %d action calls despite the one-call choice — all of "
                     "them run, in wire order (F754)", self.name, len(objects))
        stop = str(data.get("stop_reason") or "")
        # stop_details is populated only on a "refusal" (an HTTP 200); surfaced verbatim so
        # the transcript can name WHY a classifier declined.
        raw = data.get("stop_details")
        details = raw if isinstance(raw, dict) else {}
        if parsed is None and not "".join(texts).strip() and stop != "refusal":
            details = {**details, "unread": _describe(blocks)}
        return Completion(
            text="\n".join(texts),
            parsed=parsed,
            parsed_rest=rest,
            usage=_usage(data.get("usage") or {}),  # reads ~0.1x, writes ~1.25x
            stop_reason=stop,
            stop_details=details,
        )
