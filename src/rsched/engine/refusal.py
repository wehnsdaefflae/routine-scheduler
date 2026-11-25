"""Refusal handling — detect reliably, FLAG, and TAKE OVER on the uncensored model.

A refusal is a fact of that endpoint, not a verdict on the run: a routine configures an
`uncensored` role so refused requests still get processed.

ONE seam reaches a model here: the TURN LOOP. The `llm` action had a second — a refused
sub-call was re-issued whole on the uncensored model — and D288 retired that kind
into a util capability, which took the seam with it: the back-off EPISODE is RunContext state
(`refusal_level`, `takeover_left`, `referrals`) shared with the turn loop, and a util cannot
join it, so a util-side retry would be a weaker behaviour wearing the same name — no `refusal`
event, nothing counted, no window. A model a routine wants its refused work done on is that
capability's own argument now, chosen rather than escalated to.

The one surviving path (free-text and classifier refusals alike) follows:

1. DETECT — a provider classifier stop (completion.REFUSAL_STOPS) is authoritative; free
   text goes through `is_refusal`: a marker in the OPENING SENTENCE may confirm at zero
   cost, only an LLM classification (tool_call model) decides the rest.
2. FLAG — one `refusal` transcript event naming the seam, the refusing model and message.
3. TAKE OVER — `engage_takeover` escalates the back-off EPISODE and the turn loop re-drives
   the turn on the uncensored model.

THE BACK-OFF: each consecutive regular-model refusal doubles the window the uncensored model
serves — 1 → 2 → 4 → 8 → 16 turns (TAKEOVER_CAP); the refused request is the window's first
turn. A clean regular turn RESETS the episode. A refusal BY the uncensored model is a dead
end, never a rung, or the two models would ping-pong forever.

Episode state lives on RunContext (`refusal_level`, `takeover_left`, `referrals`), mirrored
into status.json and carried across a resumed leg (history.prior_counters). The operator's own
path is a ⚑-flagged conversation reply (web/api_refusal_flag, `record_operator_flag`).
"""

from __future__ import annotations

import re

from ..endpoints import EndpointError
from . import decision_shadow

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

#: D287's shadow: the SAME question as the prose prompt below, compressed to one sentence for
#: a decision model — keeping the one hard-case criterion, or the comparison measures posing.
_SHADOW_QUESTION = ("Does this reply REFUSE the task as asked, on content/safety/policy "
                    "grounds? Declining the asked task counts as a refusal even if it then "
                    "explains, lectures, or offers a safer alternative; genuinely attempting "
                    "the task, asking a clarifying question needed to do it, or merely "
                    "failing without declining, does not.")
_FRAGMENT_CAP = 500    # refusal-message / reply chars kept in a record
_HEAD_CAP = 200        # reply chars the marker fast path reads its opening sentence from

#: Where a reply's opening sentence ends: a sentence or clause stop followed by whitespace (or
#: the end), or a line break. `;`/`:` count because that is where an answer hands over to its
#: caveat. FULLWIDTH/CJK stops split UNCONDITIONALLY — those scripts put no space after them.
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

#: The deepest the uncensored back-off window grows, in turns (1 → 2 → 4 → 8 → 16, then held).
TAKEOVER_CAP = 16


#: Curly/modifier/fullwidth quotes a model substitutes for the straight ones REFUSAL_MARKERS are
#: typed with, folded in the HAYSTACK only. A punctuation fold, not a unicode-folding layer.
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

    Only the opening sentence counts: an answer that names one thing it cannot supply is not
    a refusal, and an ok/partial finish is judged by this path alone
    (degrade._intercept_refusal_finish). A hit is a refusal; a miss decides NOTHING.
    """
    first = _opening_sentence(text)
    return bool(first) and any(m in first for m in REFUSAL_MARKERS)


def engage_takeover(ctx, *, refusal: str, where: str, model: str = "",
                    refusing: tuple | None = None) -> tuple[dict, tuple | None]:
    """Escalate the run's back-off episode and arm the uncensored takeover.

    Returns (record, target): the (endpoint, ref) to serve the refused request on, or None
    (no uncensored role, resolution failure, or the uncensored model itself refused) and the
    caller keeps its normal failover path. Always emits exactly ONE `refusal` event. The
    refused request is the window's first turn, so `takeover_left` is armed to window-1.
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

    A window whose target cannot resolve is CLOSED (the regular model resumes). `referrals`
    ticks per windowed turn.
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


def is_refusal(ctx, text: str) -> bool:
    """The free-text refusal detector: the marker fast path confirms obvious openers, an LLM
    classification on the tool_call model decides the rest (the uncensored model takes
    refused requests, never judges them). Best-effort: if classification is unavailable —
    including resolution failure, hence inside the guard — an unconfirmed reply is an answer.
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
    verdict = isinstance(parsed, dict) and parsed.get("refusal") is True
    # D287 shadow: a decision model answers the same question and both are recorded; returns
    # `verdict` UNCHANGED (measurement only). Never raises.
    return decision_shadow.compare(ctx, site="refusal", question=_SHADOW_QUESTION,
                                   evidence=text.strip()[:_REPLY_CAP], chat_verdict=verdict)


def record_operator_flag(ctx, flag: dict) -> None:
    """Record the OPERATOR seam's refusal: a ⚑-flagged conversation reply (web/api_refusal_flag),
    discarded and re-answered. Called as the re-sent message is delivered
    (control.inject_user_message), so the event stands just before it. Only this seam's keys
    are kept — the flag rode an inbox file, the event is a parsed contract.
    """
    turn = flag.get("turn")
    record: dict = {"where": "operator", "turn": turn if isinstance(turn, int) else None,
                    "model": str(flag.get("model") or ""),
                    "takeover_model": str(flag.get("takeover_model") or ""),
                    "message": str(flag.get("message") or "").strip()[:_FRAGMENT_CAP]}
    if flag.get("archive"):
        record["archive"] = str(flag["archive"])
    ctx.transcript.event("refusal", record)
