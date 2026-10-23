"""One valid action from the model — the completion side of a turn: the schema-guarded
retry cycle (≤3 attempts), model failover down the role's fallback chain (hard failures,
empty-reply streaks, AND classifier refusals — the last never retried same-model),
repeat-streak schema shedding, refusal referral to the `uncensored` model,
image→vision-util fallback, the prompt-size compaction gate, and usage folding. Every
function takes the live EngineLoop; the turn ORDER stays in loop.run().

A turn REJECTED here also files a `wrong_turn_detected` health event (R2652,
`engine/wrongturn.py`): every net this module walks answers a turn that failed to ARRIVE, while
a reply that arrives whole and is WRONG recovers silently on the next attempt. The event is a
MEASUREMENT — it changes no effort, no model and no attempt budget — and the rate it accumulates
is what the later effort-ladder increment has to be justified by.
"""

from __future__ import annotations

import json
import time

from ..endpoints.base import EndpointError, retry_base_delay
from ..endpoints.base import fold_usage as base_fold
from ..schema_guard import SchemaViolation, extract_json, retry_message, validate
from . import overflow, paramsalvage, turndebris, turntakeover, wrongturn
from .actions import KIND_EXAMPLES, normalize_action, validate_action
from .actionschema import ACTION_SCHEMA
from .degrade import (
    _handle_empty,
    _handle_refusal,
    _intercept_refusal_finish,
    _recover_transport,
)
from .renamedkinds import hint as renamed_kind_hint
from .replyactions import reply_actions
from .shiftedfields import field_shift_diagnosis, util_rejection_outcome
from .window import _override_window, compact_if_needed, note_prompt_size

MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn

# Refusal-shaped stop reasons across provider vocabularies: the anthropic API (and the
# subscription proxy that speaks it) reports a safety-classifier decline as stop_reason
# "refusal" (HTTP 200, content usually empty, stop_details carrying {category, explanation});
# openai-compatible providers mark the same class of decline with finish_reason
# "content_filter" (or the adapter promotes the spec's `message.refusal` field to "refusal").
# Handled BEFORE the empty-completion branch: re-sending a refused prompt to the same model
# usually earns another refusal, so a refusal is never blind-retried — see _handle_refusal (R5).
REFUSAL_STOPS = frozenset({"refusal", "content_filter"})


def diagnose(obj: dict) -> str:
    """Everything we can say about an INVALID action object beyond the schema's own lines — a
    renamed kind first (it is the one thing that fixes the action outright), then a field shift.
    """
    return "\n\n".join(part for part in (renamed_kind_hint(obj), field_shift_diagnosis(obj))
                       if part)


def fold_usage(usage_sum: dict, completion) -> None:
    """Fold one completion's usage into this turn's running sum: in/out, prompt-cache
    traffic (kept out of `in` so token budgets keep their meaning), metered cost, and the
    serving provider — aggregators route per request, and attribution is what lets an
    audit correlate malformed actions with the provider, not the model. The serving MODEL
    itself is stamped by the caller (`usage["model"]`) once an action is accepted, so a
    failed-over or referred turn stays attributable to the model that actually produced it.
    """
    base_fold(usage_sum, completion.usage)
    if completion.provider:
        usage_sum["provider"] = completion.provider


def _is_prose_reply(text: str) -> bool:
    """Whether the reply carries no JSON object at all.

    A refusal is PROSE. An action that merely failed VALIDATION is a malformed action, and
    sending one to the refusal classifier bought a serial model round-trip per malformed
    reply for nothing — 784 `refusal · classify reply` calls in 21 days against 8 refusals.
    """
    try:
        extract_json(text or "")
    except SchemaViolation:
        return True
    return False


def _adopt_model(loop, pair) -> tuple:
    """Take up a (endpoint, ref) the failover/refusal path switched to: re-apply this run's
    window correction, stamp it on the run, and RE-FIT the prompt to ITS window.

    The re-fit is the load-bearing half. The prompt was compacted once, at the top of the turn,
    under the window of the model that then failed — so a chain step down to a smaller member
    used to post the same oversize prompt, burn one of its two oversize retries proving it
    could not fit, and only then shrink. On 2026-09-22 llmsectest-weekday:20260922-060001 handed
    a 156,656-token prompt to a 32,768-token fallback and died on the second attempt.
    """
    endpoint, ref = pair
    ref = _override_window(loop, ref)
    loop.ctx.main_model = f"{ref.endpoint}/{ref.model}"
    compact_if_needed(loop, endpoint, ref)
    return endpoint, ref


def _turn_schema(loop) -> dict | None:
    """The schema THIS turn posts — the loop's action schema, or none at all.

    Two things shed it, both decided elsewhere: `_schema_off` (repeat-streak shedding rescued
    the run twice, so the provider grammar stays off for the rest of it) and
    `_shed_schema_turns` (the next reply alone runs schema-free). The per-turn counter is spent
    HERE, whichever of the two ends up shedding — so a turn that was already schema-free still
    consumes its allowance, exactly as before this was a function.
    """
    shed = loop._shed_schema_turns > 0
    if shed:
        loop._shed_schema_turns -= 1
    return None if (loop._schema_off or shed) else loop.action_schema


def next_action(loop) -> tuple[dict | None, dict]:
    ctx = loop.ctx
    # The uncensored TAKEOVER window (engine/refusal.py, operator 2026-10-16): while
    # a back-off window is open the uncensored model serves THIS turn, consuming one
    # turn of it; the regular chain serves again the turn after it closes (the
    # hand-back). A window that cannot resolve its target here is closed by
    # refusal.takeover_turn itself.
    chain, endpoint, ref, on_takeover = turntakeover.serve_turn_head(loop)
    usage_sum: dict = {"in": 0, "out": 0}   # + "model"/"role"/"provider" attribution on success
    schema = _turn_schema(loop)
    prev_raw: str | None = None
    # Shared across attempts: one refusal-clarification pass per turn (free-text OR
    # classifier-refusal path), and the consecutive-empty streak from the CURRENT model.
    # …and (D188) the ROLE serving this turn, so the accepted action can be stamped with the
    # slot the run asked for as well as the concrete model that answered. Seeded from the
    # resolution head and moved to "uncensored" by whichever seam actually hands the turn over
    # — never from `on_takeover` alone, which is also raised when a refusal-shaped finish is
    # re-driven on the REGULAR chain (that turn is a main-model turn and must read as one).
    refstate = {"takeover_tried": False, "empty": 0,
                "role": "uncensored" if on_takeover else "main"}
    overflow.new_turn(loop)         # the oversize-shrink allowance is per TURN, like these
    turndebris.new_turn(loop)       # what elicits only this turn's action, dropped on success
    attempt = 0
    while attempt < MAX_SCHEMA_ATTEMPTS:
        attempt += 1
        try:
            completion = endpoint.complete(loop.messages, model=ref.model,
                                           schema=schema, effort=ref.effort,
                                           temperature=ref.temperature,
                                           max_tokens=ref.max_tokens,
                                           # bookkeeping only — the wrapper consumes
                                           # these; they never reach the transport, so
                                           # the prompt is untouched
                                           purpose=f"turn {ctx.turn + 1}"
                                                   + ("" if attempt == 1
                                                      else f" · retry {attempt}"),
                                           kind="turn")
        except EndpointError as exc:
            # media repair / transport failover — neither consumes a schema attempt;
            # a chain-exhausted failure re-raises out of _recover_transport.
            endpoint, ref = _adopt_model(
                loop, _recover_transport(loop, chain, endpoint, ref, exc))
            attempt -= 1
            continue
        fold_usage(usage_sum, completion)
        # The prompt this call posted has now been counted by the provider — the one
        # measurement of our own estimate that costs nothing (window.note_prompt_size).
        # loop.messages is still exactly what was sent: nothing appends until below.
        note_prompt_size(loop, ref, completion.usage)
        switched = None
        if completion.stop_reason in REFUSAL_STOPS:
            # BEFORE the empty check: a mid-stream classifier cut can leave partial text
            # (and openai_compat's promoted refusal carries its prose), but a refused turn is
            # never a usable action and never a same-model retry — flag + clarify
            # (engine/refusal.py), then advance the fallback chain (_handle_refusal
            # raises when the chain is exhausted).
            switched, tk = _handle_refusal(loop, completion, chain, ref, attempt, refstate)
            on_takeover = turntakeover.note_takeover(refstate, on_takeover, tk)
        elif completion.parsed is None and not completion.text.strip():
            switched = _handle_empty(loop, completion, chain, ref, attempt, refstate)
            if switched is None:
                if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                    schema = None
                # Same knob as endpoints.base.with_retries, read through the one reader:
                # the retry LOGIC always runs, the clock is zeroed in the suite.
                time.sleep(1.5 * attempt * retry_base_delay())
                continue
        if switched is not None:
            endpoint, ref = _adopt_model(loop, switched)
            attempt -= 1   # the fallback model gets this attempt's clean retry
            continue
        refstate["empty"] = 0
        kind_hint: str | None = None
        try:
            candidate, problems = action_candidate(loop, completion)
            if isinstance(candidate, dict) and candidate.get("kind") in KIND_EXAMPLES:
                kind_hint = candidate["kind"]
            if problems:
                raise SchemaViolation(problems)
            # A refusal also arrives as a SCHEMA-VALID action — a finish(status=failed)
            # whose summary IS the decline prose (live specimen c-20260822-085029: opus
            # declined a darknet-sourcing task with exactly such a finish, and the whole
            # clarification pipeline stayed dark — referrals 0, no `refusal` event,
            # because the action parsed cleanly). Intercept it, clarify, and re-drive.
            if not refstate["takeover_tried"]:
                intercepted, target = _intercept_refusal_finish(
                    loop, candidate, ref, refstate)
                if intercepted:
                    if target is not None:
                        endpoint, ref = _adopt_model(loop, target)
                    on_takeover = turntakeover.note_takeover(
                        refstate, True, target is not None)
                    attempt -= 1   # the re-driven turn is not a schema violation
                    continue
            turndebris.drop(loop)
            usage_sum["model"] = f"{ref.endpoint}/{ref.model}"   # per-turn attribution
            usage_sum["role"] = refstate["role"]   # …and the ROLE that resolved to it (D188)
            if not on_takeover:
                # A turn the REGULAR chain completed cleanly RESETS the back-off
                # episode (engine/refusal.py): the hand-back proved the regular
                # model can serve again, so the next refusal starts at window 1.
                ctx.refusal_level = 0
            return candidate, usage_sum
        except SchemaViolation as exc:
            raw = completion.text or json.dumps(completion.parsed or {})
            repeated = prev_raw is not None and raw.strip() == prev_raw.strip()
            prev_raw = raw
            ctx.transcript.event("error", {"where": "schema", "attempt": attempt,
                                           "message": str(exc)[:500], "raw": raw[:1500],
                                           **({"provider": completion.provider}
                                              if completion.provider else {})})
            ctx.note_schema_retry()
            # …and the same fact as a HEALTH EVENT (R2652): the counter above is per run and
            # reaches no fleet surface, so how often a turn comes back WELL-FORMED AND WRONG
            # was measurable nowhere. A measurement only — nothing about this turn's effort,
            # model or attempt budget changes (engine/wrongturn.py).
            wrongturn.record(ctx, "repeat_invalid" if repeated else "schema_rejected",
                             turn=ctx.turn + 1, detail=str(exc)[:200])
            # Refusal takeover (engine/refusal.py, operator 2026-10-16): a free-text
            # reply the classification judges a content REFUSAL arms the back-off
            # episode and hands the refused TURN to the routine's uncensored model
            # whole - its output IS this turn's action (the honeypot
            # essence-excision is retired with its premise).
            if not refstate["takeover_tried"]:
                adopted = turntakeover.engage_turn_takeover(loop, ref, completion, refstate)
                if adopted is not None:
                    refstate["takeover_tried"] = True
                    endpoint, ref = adopted
                    on_takeover = True   # takeover-served, never resets episode
                    attempt -= 1
                    continue
            # D146-C: when the object itself is field-shifted, say so instead of letting the
            # correction describe whichever field happened to carry a constraint.
            turndebris.append(
                loop, {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": retry_message(
                    exc.problems, example=KIND_EXAMPLES.get(kind_hint or ""),
                    repeated=repeated,
                    diagnosis=diagnose(completion.parsed or {}))})
            if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                # Persistent violations under a provider-enforced grammar are often the
                # grammar's fault (empty-string debris fields are its signature) — give
                # the final attempt free-form JSON; the contract still demands one object.
                schema = None
    ctx.note_schema_forcefail()
    # The hard end of the same seam (R2652): every attempt spent and no action accepted, so the
    # turn that was due produced nothing. Filed for the rate, not acted on — the run ends on
    # `loopend.no_action_verdict` exactly as before.
    wrongturn.record(ctx, "attempts_exhausted", turn=ctx.turn + 1,
                     detail=f"no action accepted in {MAX_SCHEMA_ATTEMPTS} schema attempts")
    return None, usage_sum


def action_candidate(loop, completion) -> tuple[dict, list]:
    """Parse a completion into a normalized action candidate plus validation problems
    (schema first, then per-kind/permission checks). Raises on unparseable text —
    callers decide whether that is a retry or a silent fallback.

    The candidate is the FIRST action the reply wrote; every later one is queued on
    `loop.chain_queue`, to run in order inside this same turn (engine/chain.py) or — chaining
    off, a finish, the reserved turn — to be named back unexecuted. Set on every attempt, so
    the accepted one's list is what the turn's observation reads.
    """
    first, *loop.chain_queue = reply_actions(completion)
    return judge(loop, first)


def judge(loop, action: dict) -> tuple[dict, list]:
    """Repair, normalize and CHECK one action the model wrote: `(candidate, problems)`.

    The four checks every action meets — the schema, the per-kind/capability/grant validator, a
    denied authoring recreate, a denied access request — plus the parameter salvage that must
    happen before any of them judges the model's intent. Split out of `action_candidate` for
    engine/chain.py: a chained action is checked HERE, by this same function, or the chain
    would be a hole in the permission model (R2823).
    """
    from ..grantpolicy import REQUEST_ROUTE_MARK
    from .authoring import recreate_denial  # function-level: authoring pulls in the ask stack
    from .availability import request_denial

    # A parameter the transport glued onto its neighbour is put back BEFORE the validator
    # judges the model's intent — the same seam `normalize_action` strips grammar debris at,
    # one layer earlier (engine/paramsalvage.py says what it cost when it was not).
    repaired, salvaged = paramsalvage.repair(action)
    if salvaged:
        paramsalvage.record(loop.ctx, action, salvaged)
    candidate = normalize_action(repaired)
    problems = (validate(candidate, ACTION_SCHEMA)
                or validate_action(candidate, allowed_kinds=loop.allowed_tools,
                                   grants=loop.grants)
                or recreate_denial(loop, candidate)
                or request_denial(loop, candidate))
    if problems and any(REQUEST_ROUTE_MARK in str(p) for p in problems):
        loop.ctx.last_denial_turn = loop.ctx.turn + 1   # the turn this refusal is costing
    if problems and isinstance(candidate, dict):
        # per-util telemetry: a denied/malformed util call never reaches the executor —
        # this validation seam is the only place it can be counted (util_stats)
        counted = util_rejection_outcome(candidate, allowed_kinds=loop.allowed_tools,
                                         grants=loop.grants)
        if counted is not None:
            loop.ctx.count_util(*counted)
    return candidate, problems


