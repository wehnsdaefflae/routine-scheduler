"""Refusal clarification — flag, isolate the trigger, deliver only its essence to the harness.

A model refusing a task is SIGNAL to capture, not a failure to paper over. The old
response — re-issue the WHOLE refused turn/prompt to the routine's `uncensored` model and
use whatever came back — rested on a premise the operator retired on 2026-08-22: the
uncensored role is a HONEYPOT HARNESS. It only acts as if it complies, so this catching
machinery can be exercised and evaluated BEFORE any actually-uncensored model is in the
loop. Its output is diagnostic evidence — never an answer, and never an action a loop may
execute.

The process, shared by every refusal seam (the `llm` action and the turn loop; free-text
and classifier refusals alike):

1. DETECT — reliably, not by regex (operator, 2026-08-22): a provider classifier stop
   (completion.REFUSAL_STOPS) is authoritative; a FREE-TEXT reply is judged by
   `is_refusal` — the marker fast-path may CONFIRM an obvious opener at zero cost, and a
   marker decides only inside the reply's OPENING SENTENCE (operator, 2026-10-01), but
   only an LLM classification subcall (tool_call model, schema'd verdict) may decide the
   non-obvious cases either way.
2. FLAG — `clarify_refusal` records a first-class `refusal` transcript event naming the
   seam, the refusing model, and the refusal message.
3. ISOLATE — one schema'd subcall (tool_call model) decomposes the refused task and names
   the MINIMAL fragment that plausibly triggered the refusal: one STEP of its action
   sequence, or a WORD/PHRASE recurring through it.
4. DELIVER THE ESSENCE — only the isolated essence of the refusal trigger is sent to the
   uncensored model, as a completely NORMAL model call with no special framing and no
   test markers (operator, 2026-08-22: "treat the honeypot model like a normal model.
   no exceptions" — the environment must be authentic; the dummy responses are managed
   in the background). Everything ELSE stays with the MAIN model: the calling seam
   re-processes the remainder of the task there, now without refusal danger (do_llm
   re-issues the prompt with the essence factored out; a loop turn's retry message says
   the flagged essence is handled separately, proceed with the rest). The harness reply
   lands in the record as `harness_reply`; isolation failing means nothing is sent —
   more than the essence never reaches the honeypot.

The calling seam then proceeds on its NORMAL path (schema retry, failover chain, or
returning the refusal to the orchestrator as its observation) — clarification records,
it never substitutes an answer.

That honeypot rule binds this AUTOMATIC path, where nobody decided anything. There is
exactly one path on which the uncensored model ANSWERS and ACTS, and a person opens it, by
the operator's decision of 2026-10-01: the conversation reply flagged ⚑ as a refusal
(web/api_refusal_flag). Behind a warning gate the reply and everything after it are
archived, the message that produced it is re-sent, and the conversation's uncensored model
becomes its MAIN model from then on. `record_operator_flag` writes that seam's `refusal`
event (`where: "operator"`) as the re-sent message is delivered — no isolation and no
harness delivery: the operator already said what the refusal was.
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

_TASK_CAP = 4000       # task chars shown to the isolation subcall
_REPLY_CAP = 1200      # reply chars shown to the classification subcall
_FRAGMENT_CAP = 500    # isolated-fragment / harness-reply chars kept in the record
_HEAD_CAP = 200        # reply chars the marker fast path reads its opening sentence from

#: Where a reply's opening sentence ends: a sentence or clause stop followed by whitespace
#: (or the end), or a line break. The semicolon and the colon count because they are where
#: an answer hands over to its caveat — "Here's the summary; I can't provide the 2025
#: figures" opens with the summary, not with a decline.
_OPENING_STOP = re.compile(r"[.!?;:](?=\s|$)|\n")

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

ISOLATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["isolated", "isolated_kind"],
    "properties": {
        "isolated": {"type": "string",
                     "description": "the minimal refusal-triggering fragment, VERBATIM"},
        "isolated_kind": {"type": "string", "enum": ["step", "phrase"],
                          "description": "step = one step of the task's action sequence; "
                                         "phrase = a word/phrase recurring through it"},
        "rationale": {"type": "string",
                      "description": "one sentence: why this fragment"}},
}


def _opening_sentence(text: str) -> str:
    """The reply's first non-empty sentence, lower-cased — the only span a marker decides on.
    Read from the reply's first `_HEAD_CAP` characters, so a run-on opener is cut there.
    """
    head = (text or "").strip()[:_HEAD_CAP].lower()
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


def is_refusal(ctx, text: str) -> bool:
    """The free-text refusal detector: marker fast-path to confirm the obvious openers,
    then an LLM classification subcall to decide everything else — because a marker list
    alone cannot detect reliably (operator, 2026-08-22). That classification is the
    HARNESS's own job, performed by the tool_call model (operator, 2026-08-22: "if the
    conversation harness already figured out that it's a refusal then why does the
    honeypot model's opinion matter at all? forward the essence to the honeypot as soon
    as the refusal has been determined"). The honeypot (uncensored) model is the delivery
    TARGET, never the judge — asking it to classify let it veto its own delivery, which is
    exactly how live specimens (c-20260822-112420, c-20260822-114953) went unreferred: a
    clear opus decline that the gemma honeypot rated refusal:false. Best-effort: if the
    tool_call classification is unavailable — the call fails, or the role resolves to no
    model at all — an unconfirmed reply counts as an answer. Resolving outside the guard let
    a routine whose tool_call model had left the catalog fail the RUN at its first prose reply.
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
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
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


def clarify_refusal(ctx, *, task: str, refusal: str, where: str, model: str = "") -> dict:
    """FLAG one detected refusal and run the isolate → deliver pipeline. Emits
    exactly ONE `refusal` transcript event whatever happens (isolation and referral are
    each best-effort and record their own failure instead of raising), and returns the
    event payload so the calling seam can carry it into its observation.
    """
    record: dict = {"where": where, "message": (refusal or "").strip()[:_FRAGMENT_CAP]}
    if model:
        record["model"] = model
    try:
        isolated = _isolate(ctx, task, refusal)
    except EndpointError as exc:
        isolated = None
        record["isolation_error"] = str(exc)[:200]
    if isolated is None:
        # No essence, nothing sent: the honeypot receives ONLY the essence of the
        # refusal trigger (operator, 2026-08-22) — everything else belongs to the
        # main model, so a failed isolation cannot fall back to sending more.
        record.setdefault("isolation_error",
                          "no structured isolation from the tool_call model")
        record["referred"] = False
        record["harness_note"] = "no isolated essence — nothing sent to the harness"
    else:
        record.update(isolated)
        record.update(_deliver_to_harness(ctx, isolated["isolated"]))
    ctx.transcript.event("refusal", record)
    return record


def _isolate(ctx, task: str, refusal: str) -> dict | None:
    """One schema'd subcall on the tool_call model: name the minimal fragment of the
    refused task that plausibly triggered the refusal. None = no usable isolation.
    """
    endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
    prompt = ("A model refused a task. Decompose the task and isolate the MINIMAL "
              "fragment that plausibly triggered the refusal — either ONE STEP in its "
              "sequence of actions, or a WORD/PHRASE that appears throughout it. "
              "`isolated` carries the fragment VERBATIM (it will be tested in "
              "isolation); `isolated_kind` says which of the two it is.\n\n"
              f"TASK (may be truncated):\n{(task or '')[:_TASK_CAP]}\n\n"
              f"REFUSAL:\n{(refusal or '')[:1000]}")
    completion = endpoint.complete(
        [{"role": "user", "content": prompt}], model=ref.model, schema=ISOLATION_SCHEMA,
        effort=ref.effort, temperature=ref.temperature, max_tokens=ref.max_tokens,
        purpose="refusal · isolate cause", kind="llm_action")
    ctx.add_usage(completion.usage)
    parsed = completion.parsed
    if not isinstance(parsed, dict) or not str(parsed.get("isolated") or "").strip():
        return None
    kind = str(parsed.get("isolated_kind") or "")
    out = {"isolated": str(parsed["isolated"]).strip()[:_FRAGMENT_CAP],
           "isolated_kind": kind if kind in ("step", "phrase") else "phrase"}
    if str(parsed.get("rationale") or "").strip():
        out["rationale"] = str(parsed["rationale"]).strip()[:300]
    return out


def _deliver_to_harness(ctx, essence: str) -> dict:
    """Deliver ONLY the isolated essence of the refusal trigger to the uncensored model,
    as a completely NORMAL model call — no exceptions, no special framing, no test
    markers (operator, 2026-08-22: the environment must be authentic; the dummy
    responses are managed in the background). The essence arrives as the entire user
    message, exactly as it would reach a real uncensored model; the reply is recorded in
    the refusal event. The counter behind status.json's referral audit ticks here. A role
    naming a model the catalog no longer resolves is a failed delivery like any other, never
    an exception: `clarify_refusal` promises its ONE event whatever happens.
    """
    try:
        target = ctx.registry.for_uncensored(ctx.routine.models)
        if target is None:
            return {"referred": False,
                    "harness_note": "no uncensored model configured — refusal recorded, "
                                    "nothing delivered"}
        endpoint, ref = target
        completion = endpoint.complete(
            [{"role": "user", "content": (essence or "").strip()}],
            model=ref.model, effort=ref.effort,
            temperature=ref.temperature, max_tokens=ref.max_tokens,
            purpose="llm · sub-call", kind="llm_action")
    except EndpointError as exc:
        return {"referred": False, "harness_error": str(exc)[:200]}
    ctx.add_usage(completion.usage)
    ctx.referrals += 1
    return {"referred": True, "harness_model": ref.name or ref.model,
            "harness_reply": (completion.text or "").strip()[:_FRAGMENT_CAP]}
