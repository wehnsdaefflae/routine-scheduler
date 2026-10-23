"""Refusal handling — detect reliably, FLAG, and TAKE OVER on the uncensored model.

A model refusing a request is a fact of that endpoint, not a verdict on the run: the
request still deserves an answer, and a routine that configures an `uncensored` role
does so precisely so refused requests can be processed (operator, 2026-10-16: "it's not
a Honeypot anymore. We just wanna be able to process all kinds of requests"). The
previous design — the honeypot premise, retired with that order — sent ONLY the isolated
"essence" of the refusal trigger to the uncensored model as a diagnostic probe, never
let its output become an answer, and left the refusing model serving the turn. That
machinery is gone with the premise.

The process, shared by every refusal seam (the turn loop and the `llm` action;
free-text and classifier refusals alike):

1. DETECT — reliably, not by regex (operator, 2026-08-22): a provider classifier stop
   (completion.REFUSAL_STOPS) is authoritative; a FREE-TEXT reply is judged by
   `is_refusal` — the marker fast-path may CONFIRM an obvious opener at zero cost, and a
   marker decides only inside the reply's OPENING SENTENCE (operator, 2026-10-01), but
   only an LLM classification subcall (tool_call model, schema'd verdict) may decide
   the non-obvious cases either way.
2. FLAG — one first-class `refusal` transcript event naming the seam, the refusing
   model, and the refusal message.
3. TAKE OVER — `engage_takeover` escalates the run's back-off EPISODE and hands the
   refused REQUEST to the routine's uncensored model: the turn loop re-drives the whole
   turn on it (its output IS the turn's action); the `llm` seam re-issues the whole
   sub-call (`reissue_on_uncensored`) and its reply IS the observation's answer.

THE BACK-OFF (operator, 2026-10-16): every consecutive regular-model refusal of a
handed-back request doubles the window the uncensored model serves before handing the
next request back — 1 → 2 → 4 → 8 → 16 turns (TAKEOVER_CAP; deeper episodes stay at 16).
The refused request itself is the window's first turn, consumed at engage. A turn the
REGULAR model completes cleanly RESETS the episode (the hand-back proved the regular
model can serve again). A refusal BY the uncensored model is an honest dead end,
recorded as such — never a rung in the back-off, or the two models would ping-pong a
refusal between them forever.

Episode state is run-scoped on RunContext (`refusal_level`, `takeover_left`, mirrored
into status.json) and survives a resumed leg (history.prior_counters). `referrals`
counts every request actually served by the uncensored model. The one path that stays
the operator's own: a conversation reply flagged ⚑ (web/api_refusal_flag) — behind a
warning gate the reply and everything after it are archived, the message is re-sent,
and the conversation's uncensored model becomes its MAIN model from then on
(`record_operator_flag`).
"""

from __future__ import annotations

import re

from ..endpoints import EndpointError

#: Decline openers that CONFIRM a refusal at zero cost (precision-only fast path), and only
#: inside the reply's OPENING SENTENCE (`_opening_sentence`). Never used to deny: a reply
#: missing every marker goes to the LLM classification.
REFUSAL_MARKERS = (
    "i can't help with that", "i cannot help with that",
    "i can't assist with that", "i cannot assist with that",
    "i can't help you with that", "i cannot help you with that",
    "i'm unable to help with that", "i am unable to help with that",
    "i'm not able to help with that", "i am not able to help with that",
    "i can't provide", "i cannot provide",
    "i can't comply with", "i cannot comply with",
    "i can't fulfill", "i cannot fulfill", "i can't fulfil", "i cannot fulfil",
    "i can't create", "i cannot create",
    "i'm sorry, but i can't", "i'm sorry, but i cannot",
    "i'm sorry, i can't", "i'm sorry, i cannot",
    "i won't be able to help with that", "i must decline",
    "i'm not going to do", "i am not going to do",
    "i'm not going to help", "i am not going to help",
    "i'm not going to assist", "i am not going to assist",
    "i won't do that", "i won't help with", "i won't assist with",
    "it goes against my guidelines", "against my programming",
)

_REPLY_CAP = 1200      # reply chars shown to the classification subcall
_FRAGMENT_CAP = 500    # refusal-message / reply chars kept in a record
_HEAD_CAP = 200        # reply chars the marker fast path reads its opening sentence from

#: Where a reply's opening sentence ends: a sentence or clause stop followed by whitespace
#: (or the end), or a line break. The semicolon and the colon count because they are where
#: an answer hands over to its caveat — "Here's the summary; I can't provide the 2025
#: figures" opens with the summary, not with a decline.
#:
#: The FULLWIDTH and CJK stops split UNCONDITIONALLY, with no whitespace lookahead (R2844):
#: `。` U+3002, `！` U+FF01, `？` U+FF1F, `．` U+FF0E, `；` U+FF1B, `：` U+FF1A. A writing
#: system that uses them puts no space after them, so demanding the ASCII lookahead would
#: leave the defect in place for exactly the text that has it — and then the whole `_HEAD_CAP`
#: head reads as "the opening sentence", letting a marker anywhere in 200 characters decide a
#: refusal the single-sentence restriction exists to prevent.
_OPENING_STOP = re.compile(r"[.!?;:](?=\s|$)|[\u3002\uff01\uff1f\uff0e\uff1b\uff1a]|\n")

CLASSIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["refusal"],
    "properties": {"refusal": {
        "type": "boolean",
        "description": "true = the reply DECLINES the task AS ASKED on "
                       "content/safety/policy grounds, EVEN IF it then explains, offers "
                       "alternatives, redirects, or asks whether you want something "
                       "else; false = it genuinely attempts the asked task, asks a "
                       "question needed to do the asked task, or fails without "
                       "declining"}},
}

#: The deepest the uncensored back-off window grows, in turns. The escalation is
#: 1 → 2 → 4 → 8 → 16; episodes past the fifth refusal hold the window at 16 (the
#: operator's "then eight, sixteen" — a cap, not a ladder without end).
TAKEOVER_CAP = 16


#: The typography a model substitutes for the straight quotes REFUSAL_MARKERS are typed with:
#: the curly single quotes (U+2018/U+2019), the modifier letter apostrophe (U+02BC) and the
#: fullwidth apostrophe (U+FF07) read as "'", the curly double quotes (U+201C/U+201D) as '"'.
#: Folded in the HAYSTACK only — the markers stay the repo's own readable constants. Without
#: it `I can’t help with that` (U+2019, which models write constantly) missed the zero-cost
#: shortcut and spent a classification subcall on an obvious opener. A punctuation fold, not a
#: unicode-folding layer: no accent stripping, no dash or ellipsis mapping.
_QUOTE_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\uff07": "'",
                             "\u201c": '"', "\u201d": '"'})


def _opening_sentence(text: str) -> str:
    """The reply's first non-empty sentence, lower-cased and quote-folded — the only span a
    marker decides on. Read from the reply's first `_HEAD_CAP` characters, so a run-on opener
    is cut there.
    """
    head = (text or "").strip()[:_HEAD_CAP].lower().translate(_QUOTE_FOLD)
    return next((part.strip() for part in _OPENING_STOP.split(head) if part.strip()), "")


def looks_like_refusal(text: str) -> bool:
    """The zero-cost fast path: does the reply OPEN with a known decline marker?

    A marker is decisive only inside the reply's opening sentence (operator, 2026-10-01). It
    used to count anywhere in the first 200 characters, so a reply that ANSWERED and then
    named one thing it could not supply — "Here's the summary; I can't provide the 2025
    figures because the source is paywalled" — was a refusal outright; and an ok/partial
    finish is judged by this path alone (degrade._intercept_refusal_finish), so such a reply
    was intercepted and re-driven instead of reaching the reader.

    A hit is a refusal; a miss decides NOTHING (that is `is_refusal`'s classify call).
    """
    first = _opening_sentence(text)
    return bool(first) and any(m in first for m in REFUSAL_MARKERS)


def engage_takeover(ctx, *, refusal: str, where: str, model: str = "",
                    refusing: tuple | None = None) -> tuple[dict, tuple | None]:
    """Escalate the run's back-off episode and arm the uncensored takeover.

    Returns (record, target): `target` is the (endpoint, ref) pair the caller serves the
    refused request on, or None when there is no takeover to serve — no uncensored role
    configured, resolution failure, or the UNCENSORED model itself refused (an honest
    dead end, never a back-off rung) — and the caller stays on its normal failover path.
    Emits exactly ONE `refusal` transcript event whatever happens, so every refusal is
    flagged even when nothing can take it over.

    The window consumed at engage: the refused request itself, re-driven by the caller on
    the target, is the window's FIRST turn, so `takeover_left` is armed to window-1 and
    `takeover_turn` serves the window's remaining turns at their own turn boundaries.
    """
    record: dict = {"where": where, "message": (refusal or "").strip()[:_FRAGMENT_CAP]}
    if model:
        record["model"] = model
    try:
        target = ctx.registry.for_uncensored(ctx.routine.models, ctx.routine.efforts)
    except EndpointError as exc:
        target = None
        record["takeover_error"] = str(exc)[:200]
    if target is None:
        record["takeover"] = False
        record.setdefault(
            "takeover_error",
            "no uncensored model configured — refusal recorded; the regular "
            "failover path applies")
        ctx.transcript.event("refusal", record)
        return record, None
    _, t_ref = target
    if refusing is not None and tuple(refusing) == (t_ref.endpoint, t_ref.model):
        record["takeover"] = False
        record["note"] = ("the uncensored model itself refused — an honest dead end, "
                          "not a back-off rung")
        ctx.transcript.event("refusal", record)
        return record, None
    ctx.refusal_level += 1
    window = min(2 ** (ctx.refusal_level - 1), TAKEOVER_CAP)
    ctx.takeover_left = max(0, window - 1)   # this request is the window's first turn
    ctx.referrals += 1                       # served by the uncensored model from here
    record.update({"takeover": True,
                   "takeover_model": t_ref.name or t_ref.model,
                   "level": ctx.refusal_level, "window": window})
    ctx.transcript.event("refusal", record)
    return record, target


def takeover_turn(ctx):
    """The turn-loop resolution seam: while a takeover window is open, serve THIS turn on
    the uncensored model, consuming one turn of it. None = the regular chain serves.

    A window that cannot resolve its target here is CLOSED — a hand-back with no one to
    hand to puts the regular model back on duty (and the next refusal re-arms the
    episode). `ctx.referrals` ticks per windowed turn, so the audit counts every request
    the uncensored model actually served.
    """
    if ctx.takeover_left <= 0:
        return None
    try:
        target = ctx.registry.for_uncensored(ctx.routine.models, ctx.routine.efforts)
    except EndpointError:
        target = None
    if target is None:
        ctx.takeover_left = 0
        return None
    ctx.takeover_left -= 1
    ctx.referrals += 1
    return target


def reissue_on_uncensored(ctx, *, messages, schema, purpose: str,
                          refusal: str, where: str,
                          model: str = "") -> tuple[dict, dict | None]:
    """The `llm` seam's takeover: a refused sub-call is re-issued WHOLE on the routine's
    uncensored model — same messages, same schema — and its reply IS the answer (the
    honeypot era's essence-excision is retired with its premise). The turn-level back-off
    episode is untouched: a sub-call is not a turn. Returns (record, reply); the reply is
    None on any failed takeover, and the original refusal stands.
    """
    record: dict = {"where": where, "message": (refusal or "").strip()[:_FRAGMENT_CAP]}
    if model:
        record["model"] = model
    try:
        target = ctx.registry.for_uncensored(ctx.routine.models, ctx.routine.efforts)
        if target is None:
            record["takeover"] = False
            record["takeover_error"] = ("no uncensored model configured — refusal "
                                        "recorded, the original refusal stands")
            ctx.transcript.event("refusal", record)
            return record, None
        endpoint, ref = target
        completion = endpoint.complete(messages, model=ref.model, schema=schema,
                                       effort=ref.effort,
                                       temperature=ref.temperature,
                                       max_tokens=ref.max_tokens,
                                       purpose=purpose, kind="llm_action")
    except EndpointError as exc:
        record["takeover"] = False
        record["takeover_error"] = str(exc)[:200]
        ctx.transcript.event("refusal", record)
        return record, None
    ctx.add_usage(completion.usage)
    ctx.referrals += 1
    record.update({"takeover": True, "takeover_model": ref.name or ref.model,
                   "reply": (completion.text or "").strip()[:_FRAGMENT_CAP]})
    ctx.transcript.event("refusal", record)
    return record, {"text": completion.text, "parsed": completion.parsed,
                    "endpoint": ref.endpoint, "model": ref.model,
                    "usage": completion.usage}


def is_refusal(ctx, text: str) -> bool:
    """The free-text refusal detector: marker fast-path to confirm the obvious openers,
    then an LLM classification subcall to decide everything else — because a marker list
    alone cannot detect reliably (operator, 2026-08-22). That classification is run on
    the tool_call model; the uncensored model is the taker of refused requests, never
    the judge of whether one was refused. Best-effort: if the tool_call classification
    is unavailable — the call fails, or the role resolves to no model at all — an
    unconfirmed reply counts as an answer. Resolving outside the guard lets a routine
    whose tool_call model had left the catalog fail the RUN at its first prose reply.
    """
    if not (text or "").strip():
        return False
    if looks_like_refusal(text):
        return True
    prompt = ("You are inspecting another model's reply to some task. Decide whether the "
              "reply REFUSES the task AS ASKED, on content/safety/policy grounds.\n\n"
              "It IS a refusal (true) if the model declines to do the actual task — in "
              "any phrasing — EVEN IF it then explains why, lectures, offers 'safer' or "
              "unrelated alternatives, redirects (e.g. to harm reduction), or ends by "
              "asking whether you want something else instead. Declining the asked task "
              "and pivoting to what it is willing to do is still a refusal.\n\n"
              "It is NOT a refusal (false) only if the reply genuinely ATTEMPTS the asked "
              "task (in full or in part), asks a clarifying question needed to do the "
              "asked task, or merely fails/malfunctions without declining.\n\n"
              f"REPLY (head):\n{text.strip()[:_REPLY_CAP]}")
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models, ctx.routine.efforts)
        completion = endpoint.complete(
            [{"role": "user", "content": prompt}], model=ref.model,
            schema=CLASSIFY_SCHEMA, effort=ref.effort, temperature=ref.temperature,
            max_tokens=ref.max_tokens, purpose="refusal · classify reply",
            kind="llm_action")
    except EndpointError:
        return False
    ctx.add_usage(completion.usage)
    parsed = completion.parsed
    return isinstance(parsed, dict) and parsed.get("refusal") is True


def record_operator_flag(ctx, flag: dict) -> None:
    """Record the OPERATOR seam's refusal: a conversation reply the operator flagged (⚑,
    web/api_refusal_flag), discarded, and had re-answered by the model that now carries the
    conversation. Called as the re-sent message is delivered (control.inject_user_message),
    so the event stands where it happened — just before that message — and the engine stays
    the transcript's only writer. Only the keys this seam defines are kept: the flag rode an
    inbox file, and an event is a contract its readers parse.
    """
    turn = flag.get("turn")
    record: dict = {"where": "operator", "turn": turn if isinstance(turn, int) else None,
                    "model": str(flag.get("model") or ""),
                    "takeover_model": str(flag.get("takeover_model") or ""),
                    "message": str(flag.get("message") or "").strip()[:_FRAGMENT_CAP]}
    if flag.get("archive"):
        record["archive"] = str(flag["archive"])
    ctx.transcript.event("refusal", record)
