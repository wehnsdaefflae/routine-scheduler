"""One valid action from the model — the completion side of a turn: the schema-guarded
retry cycle (≤3 attempts), model failover down the role's fallback chain (hard failures,
empty-reply streaks, AND classifier refusals — the last never retried same-model),
repeat-streak schema shedding, refusal referral to the `uncensored` model,
image→vision-util fallback, the prompt-size compaction gate, and usage folding. Every
function takes the live EngineLoop; the turn ORDER stays in loop.run().

A turn REJECTED here also files a `wrong_turn_detected` health event (`engine/wrongturn.py`). It
is a MEASUREMENT only: it changes no effort, model or attempt budget.
"""

from __future__ import annotations

import json
import time

from ..endpoints.base import EndpointError, retry_base_delay
from ..endpoints.usage import fold_usage as base_fold
from ..schema_guard import SchemaViolation, extract_json, retry_message, validate
from . import denialmemo, overflow, paramsalvage, schemamemo, turndebris, turntakeover, wrongturn
from .actions import KIND_EXAMPLES, normalize_action, validate_action
from .actionschema import ACTION_SCHEMA
from .completionrequest import complete_attempt
from .degrade import (
    _handle_empty,
    _handle_refusal,
    _intercept_refusal_finish,
    _recover_transport,
)
from .renamedkinds import hint as renamed_kind_hint
from .replyactions import reply_actions
from .shiftedfields import field_shift_diagnosis, unkind_diagnosis, util_rejection_outcome
from .window import _override_window, compact_if_needed, note_prompt_size

MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn

# Refusal-shaped stop reasons: anthropic's "refusal", openai-compatible "content_filter" (or a
# promoted `message.refusal`). Checked BEFORE the empty branch, and never retried same-model —
# see _handle_refusal.
REFUSAL_STOPS = frozenset({"refusal", "content_filter"})


def diagnose(obj: dict, loop=None) -> str:
    """Everything we can say about an INVALID action beyond the schema's lines, in reading
    order: a renamed kind, a kind that is really a field or a util, then a field shift
    (`shiftedfields.unkind_diagnosis` records why these three cannot overlap, and what `loop`
    buys the util branch: the live catalog instead of a heuristic).
    """
    return "\n\n".join(part for part in (renamed_kind_hint(obj), unkind_diagnosis(obj, loop),
                                         field_shift_diagnosis(obj)) if part)


def fold_usage(usage_sum: dict, completion) -> None:
    """Fold one completion's usage into this turn's running sum: in/out, cache traffic (kept
    out of `in`), cost, and the serving provider (aggregators route per request). The serving
    MODEL is stamped by the caller once an action is accepted.
    """
    base_fold(usage_sum, completion.usage)
    if completion.provider:
        usage_sum["provider"] = completion.provider


def _is_prose_reply(text: str) -> bool:
    """Whether the reply carries no JSON object at all.

    A refusal is PROSE; an action that merely failed VALIDATION is malformed and must not cost
    a refusal-classifier round-trip.
    """
    try:
        extract_json(text or "")
    except SchemaViolation:
        return True
    return False


def _adopt_model(loop, pair) -> tuple:
    """Take up a (endpoint, ref) the failover/refusal path switched to: re-apply this run's
    window correction, stamp it on the run, and RE-FIT the prompt to ITS window.

    The re-fit is load-bearing: the prompt was compacted under the FAILED model's window, and a
    smaller fallback must not be handed it oversize.
    """
    endpoint, ref = pair
    ref = _override_window(loop, ref)
    loop.ctx.main_model = f"{ref.endpoint}/{ref.model}"
    compact_if_needed(loop, endpoint, ref)
    return endpoint, ref


def _turn_schema(loop) -> dict | None:
    """The schema THIS turn posts — the loop's action schema, or none at all.

    Shed by `_schema_off` (off for the rest of the run) or `_shed_schema_turns` (the next
    reply only). The per-turn counter is spent HERE whichever of the two sheds.
    """
    shed = loop._shed_schema_turns > 0
    if shed:
        loop._shed_schema_turns -= 1
    return None if (loop._schema_off or shed) else loop.action_schema


def _record_rejection(loop, exc, completion, *, attempt: int, repeated: bool,
                      raw: str) -> str:
    """File this rejection everywhere it is recorded, and return what the run already knew.

    Three records: the transcript `error` event, the per-run counter, and the
    `wrong_turn_detected` health event. Returns `schemamemo.recall`'s sentence naming the turn
    this rejection was FIRST earned at ("" the first time) — consulted before the records are
    written, so the transcript can say so.
    """
    ctx = loop.ctx
    recalled = schemamemo.recall(loop, exc.problems, turn=ctx.turn + 1)
    ctx.transcript.event("error", {"where": "schema", "attempt": attempt,
                                   "message": str(exc)[:500], "raw": raw[:1500],
                                   **({"recalled": True} if recalled else {}),
                                   **({"provider": completion.provider}
                                      if completion.provider else {})})
    ctx.note_schema_retry()
    wrongturn.record(ctx, "repeat_invalid" if (repeated or recalled) else "schema_rejected",
                     turn=ctx.turn + 1, detail=str(exc)[:200])
    return recalled


def next_action(loop) -> tuple[dict | None, dict]:
    ctx = loop.ctx
    # While an uncensored TAKEOVER window is open (engine/refusal.py) that model serves THIS
    # turn; the regular chain serves again once it closes.
    chain, endpoint, ref, on_takeover = turntakeover.serve_turn_head(loop)
    usage_sum: dict = {"in": 0, "out": 0}   # + "model"/"role"/"provider" attribution on success
    schema = _turn_schema(loop)
    prev_raw: str | None = None
    # Shared across attempts: one refusal-clarification pass per turn, the consecutive-empty
    # streak, and (D188) the ROLE serving this turn — moved to "uncensored" only by the seam
    # that actually hands the turn over, never from `on_takeover` alone.
    refstate = {"takeover_tried": False, "empty": 0,
                "role": "uncensored" if on_takeover else "main"}
    overflow.new_turn(loop)         # the oversize-shrink allowance is per TURN, like these
    turndebris.new_turn(loop)       # what elicits only this turn's action, dropped on success
    attempt = 0
    while attempt < MAX_SCHEMA_ATTEMPTS:
        attempt += 1
        try:
            completion = complete_attempt(loop, endpoint, ref, schema, attempt=attempt)
        except EndpointError as exc:
            # media repair / transport failover — neither consumes a schema attempt;
            # a chain-exhausted failure re-raises out of _recover_transport.
            endpoint, ref = _adopt_model(
                loop, _recover_transport(loop, chain, endpoint, ref, exc))
            attempt -= 1
            continue
        fold_usage(usage_sum, completion)
        # The provider's count of the prompt just posted calibrates our estimate; loop.messages
        # is still exactly what was sent.
        note_prompt_size(loop, ref, completion.usage)
        switched = None
        if completion.stop_reason in REFUSAL_STOPS:
            # A refused turn is never a usable action nor a same-model retry, even with partial
            # text: clarify, then advance the chain (raises when it is exhausted).
            switched, tk = _handle_refusal(loop, completion, chain, ref, attempt, refstate)
            on_takeover = turntakeover.note_takeover(refstate, on_takeover, tk)
        elif completion.parsed is None and not completion.text.strip():
            switched = _handle_empty(loop, completion, chain, ref, attempt, refstate)
            if switched is None:
                if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                    schema = None
                # Same knob as endpoints.base.with_retries (zeroed in the suite).
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
            # A refusal can also arrive SCHEMA-VALID — a finish(status=failed) whose summary
            # is the decline prose. Intercept it, clarify, and re-drive.
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
                # A clean regular-chain turn RESETS the refusal back-off episode.
                ctx.refusal_level = 0
            return candidate, usage_sum
        except SchemaViolation as exc:
            raw = completion.text or json.dumps(completion.parsed or {})
            repeated = prev_raw is not None and raw.strip() == prev_raw.strip()
            prev_raw = raw
            recalled = _record_rejection(loop, exc, completion, attempt=attempt,
                                         repeated=repeated, raw=raw)
            # A free-text reply judged a content REFUSAL hands the whole turn to the routine's
            # uncensored model (engine/refusal.py); its output IS this turn's action.
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
                    diagnosis=diagnose(completion.parsed or {}, loop),
                    help_text=schemamemo.field_help(exc.problems),
                    recalled=recalled)})
            if recalled:
                # A recalled rejection drops the grammar now (F736): a constrained decoder is
                # the likeliest author of the repeat (`engine/schemamemo.py`).
                schema = None
            if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                # Persistent violations under a provider-enforced grammar are often the
                # grammar's fault (empty-string debris fields are its signature) — give
                # the final attempt free-form JSON; the contract still demands one object.
                schema = None
    ctx.note_schema_forcefail()
    # Every attempt spent, no action accepted: filed for the rate only; the run ends on
    # `loopend.no_action_verdict`.
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
    denied authoring recreate, a denied access request — after parameter salvage. A chained
    action (engine/chain.py) is checked by this same function, or the chain would be a hole in
    the permission model.
    """
    from ..grantpolicy import REQUEST_ROUTE_MARK
    from .authoring import recreate_denial  # function-level: authoring pulls in the ask stack
    from .availability import request_denial

    # A parameter the transport glued onto its neighbour is put back BEFORE validation.
    repaired, salvaged = paramsalvage.repair(action)
    if salvaged:
        paramsalvage.record(loop.ctx, action, salvaged)
    candidate = normalize_action(repaired)
    problems = (validate(candidate, ACTION_SCHEMA)
                or validate_action(candidate, allowed_kinds=loop.allowed_tools,
                                   grants=loop.grants)
                or recreate_denial(loop, candidate)
                or request_denial(loop, candidate))
    problems = denialmemo.answer(loop, candidate, problems)
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


