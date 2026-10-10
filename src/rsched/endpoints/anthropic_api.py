"""Anthropic Messages API adapter.

Schema enforcement via tool use: one tool named "action" whose input_schema is the requested
schema, offered on `auto` held to ONE call (`_ONE_CALL_AT_MOST`). Without a schema it is a plain
messages call.

**Why not forced.** A forced `tool_choice` makes a configured EFFORT meaningless: the Messages
API allows thinking only on `auto`, so CLIProxyAPI — the subscription proxy every `anthropic`
endpoint here runs through — strips `thinking` and `output_config.effort` from every forced call,
and the newest Claude models on the direct API refuse forcing outright. Measured through the
proxy on 2026-10-08: forced, Opus 5 at effort `low` and `max` answered with 467 and 551 output
tokens and no thinking block — the setting reached nothing; on `auto` the same calls thought and
answered with the action under its own name, on Opus 5, Sonnet 5 and Fable 5, on a first turn and
a third, with the composer's real 33k-character system prompt and the full action schema.
0.370.2–0.372.2 forced the tool for every call, so for a week every "Opus high" or "Sonnet max"
in the catalog ran at the proxy's default.

**Why it stays safe.** 0.372.0 had offered `auto` once before and Opus then answered `stop_reason:
tool_use` with nothing the adapter could read; that shape was never reproduced, but it cannot cost
a run again: an `auto` reply no action can be read from is RE-ASKED once, forced
(`_reask_forced`), inside the same call — that one turn runs at the proxy's default, the
completion says so in `stop_details["forced_reask"]` (with what the unread reply carried), its
usage is the sum of both requests, and a warning is logged. An endpoint whose models were
validated on the forced route (the Codex models the proxy serves on this wire) keeps it with
`tool_choice: forced` on its config.

Every optional field a model may refuse — the forced `tool_choice`, `output_config` (effort),
`temperature`, the `cache_control` markers — rides the body, and a 400 that NAMES one degrades
it for a retry (`_degrade`). This adapter's KIND is a WIRE, not a provider: a subscription
proxy speaks it while serving `gpt-*` ids, Haiku 4.5 still honours temperature, and the newest
Claude models on the direct API (Fable 5.1, Opus 5.5, Sonnet 5.5) refuse forced tool use — on
those the tool is still offered, on `auto` with at most one call (`_ONE_CALL_AT_MOST`: the engine
takes ONE action per turn), and the engine reads an action from a text reply when the model
writes one instead. So no field is sent blindly or dropped blindly: the model that rejects one
says so, and pays a round trip per refused field.

**Past actions are sent as CALLS** (`anthropic_messages.native_turns`, 0.407.0). On `auto`, a
thinking Opus 5 that reads its own history as JSON text writes its next action as text too —
which ends nothing — thinks on as though it had run, and makes the call for the one after: two
actions in one reply, the second narrating the first as done. A schema'd call on `auto` therefore
renders each past action as a `tool_use` of the action tool and the message after it as that
call's `tool_result`, and a reply ends at its one call. `text` and `parsed` both still reach the
engine, which runs a reply's FIRST action and names every later one back unexecuted
(engine/replyactions.py, 0.406.0) — the safety net under the rendering. Before 0.406.0 the
engine read only `parsed`: the first action vanished and the second, narrating it, ran.

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
a 1,000-char leading slice of its first text (`anthropic_messages.claim_placement`). Cache
traffic is reported as usage "cached_in" / "cache_write" (kept out of "in"). A 400 naming
cache_control gets one degraded retry without the markers.

Multimodal: a message may carry a `media` list ([{path, media_type}]); this API takes
images and PDFs natively, so those files become base64 image/document content blocks. Image
blocks are cache-eligible like text, so a viewed image re-reads at cache-read weight too.
Every message-level rendering — merging, media, native calls, cache markers — lives in
`anthropic_messages.py`; this module is the transport.
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
    supports_media_type,
    with_retries,
)

log = logging.getLogger("rsched.endpoints.anthropic")

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

#: The forced tool_choice: what an endpoint configured `tool_choice: forced` sends, and the
#: one re-ask of an unreadable `auto` reply.
_FORCED = {"type": "tool", "name": "action"}

#: The tool_choice every schema'd call sends by default (the module docstring says why): the API
#: default, held to ONE call — `auto` alone would let a reply carry several. `_parse` reads the
#: first call; a reply's text can still carry actions of its own (engine/replyactions.py).
_ONE_CALL_AT_MOST = {"type": "auto", "disable_parallel_tool_use": True}


def _summed(a: dict, b: dict) -> dict:
    """Two requests' usage as one: a re-asked turn cost both, and a budget reading only the
    second would undercount it.
    """
    return {k: int(a.get(k) or 0) + int(b.get(k) or 0) for k in {*a, *b}}


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
        out = strip_cache_control(out)
    return out if out != body else None


class AnthropicEndpoint:
    """Anthropic-compatible Messages adapter; billing belongs to the upstream. Schema via
    a single tool on `auto` (forced only where the endpoint says so); effort via `output_config`
    and `temperature` when configured — each optional field degraded on a 400 naming it.
    """

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = cfg.api_key
        self.key_env_file = cfg.key_env_file
        self.key_var = cfg.key_var
        self.temperature = cfg.temperature
        self.tool_choice = cfg.tool_choice

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
            body["tool_choice"] = dict(_FORCED if self.tool_choice == "forced"
                                       else _ONE_CALL_AT_MOST)
        if not cacheable:
            body = claim_placement(body)
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
            completion = self._parse(resp)
            if "unread" in completion.stop_details and sent.get("tool_choice") != _FORCED \
                    and "tools" in sent:
                return self._reask_forced(sent, headers, timeout, completion)
            return completion

        return with_retries(call)

    def _reask_forced(self, sent: dict, headers: dict, timeout: int,
                      unread: Completion) -> Completion:
        """An `auto` reply no action could be read from, asked ONCE more with the tool forced —
        the shape 0.372.0 met through the proxy (module docstring). The forced answer runs at
        the proxy's default effort, so it is marked, not passed off as the configured one; when
        the re-ask fails or is unreadable too, the first reply is returned as it was and the
        engine's empty-completion handling takes over, exactly as without the re-ask.
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
                          usage=_summed(unread.usage, again.usage),
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
        if len(calls) > 1:
            # `_ONE_CALL_AT_MOST` asks the API for at most one; a wire that drops the field
            # gets the FIRST — the engine runs a reply's first action (engine/replyactions.py).
            log.warning("%s: a reply carried %d action calls despite the one-call choice — "
                        "the first is read", self.name, len(calls))
        parsed = calls[0] if calls and isinstance(calls[0], dict) else None
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
