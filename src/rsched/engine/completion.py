"""One valid action from the model — the completion side of a turn: the schema-guarded
retry cycle (≤3 attempts), model failover down the role's fallback chain (hard failures,
empty-reply streaks, AND classifier refusals — the last never retried same-model),
repeat-streak schema shedding, refusal referral to the `uncensored` model,
image→vision-util fallback, the prompt-size compaction gate, and usage folding. Every
function takes the live EngineLoop; the turn ORDER stays in loop.run().
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import time
from pathlib import Path

from ..endpoints import failover
from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from ..schema_guard import SchemaViolation, extract_json, retry_message, validate
from . import executor, fileops
from .actions import (
    ACTION_SCHEMA,
    KIND_EXAMPLES,
    normalize_action,
    util_rejection_outcome,
    validate_action,
)
from .history import (
    CHARS_PER_TOKEN,
    KEEP_HEAD_MSGS,
    KEEP_TAIL_MSGS,
    clamp_to_cap,
    compact_to_history,
    input_cap_chars,
    maybe_compact,
    messages_size,
)

MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn

# Refusal-shaped stop reasons across provider vocabularies: anthropic and the claude CLI
# report a safety-classifier decline as stop_reason "refusal" (HTTP 200, content usually
# empty, stop_details carrying {category, explanation}); openai-compatible providers mark
# the same class of decline with finish_reason "content_filter" (or the adapter promotes
# the spec's `message.refusal` field to "refusal"). Handled BEFORE the empty-completion
# branch: re-sending a refused prompt to the same model usually earns another refusal, so
# a refusal is never blind-retried — see _handle_refusal (R5).
REFUSAL_STOPS = frozenset({"refusal", "content_filter"})

# F278: the window guard. The clamp (`clamp_to_cap`) sizes everything from the CATALOG's
# context figure — when that figure claims a larger window than the provider actually
# enforces, no compaction gate ever fires and the completion 400s with
# context_length_exceeded (2026-08-05: a gemma entry raised to 250k tokens against the
# provider's real 65,536 disarmed the whole net and killed two live conversations). The
# guard closes the loop at the error itself: parse the provider's STATED maximum from the
# overflow text, shrink this RUN's view of that model's window to it, re-clamp the prompt,
# and retry the same model once. Config stays authoritative for sizing DOWN (a smaller
# configured window is a deliberate budget); the provider is authoritative for sizing UP —
# a stated max at or above the configured window means config wasn't the problem, so the
# guard declines and the ordinary transport nets take over.
_OVERFLOW_HINTS = ("context_length_exceeded", "maximum context length", "context window")
_OVERFLOW_TOKENS_RE = re.compile(
    r"(?:maximum context length(?: is)?|context (?:window|length) of(?: only)?|"
    r"context_length_exceeded\D{0,40}?)\s*(\d{4,7})\s*tokens", re.IGNORECASE)


def parse_overflow_limit(text: str) -> int | None:
    """The provider-stated maximum context TOKENS from a context-overflow error message,
    or None when the text is not an overflow error (or states no usable figure).
    """
    low = text.lower()
    if not any(h in low for h in _OVERFLOW_HINTS):
        return None
    m = _OVERFLOW_TOKENS_RE.search(text)
    return int(m.group(1)) if m else None


def _override_window(loop, ref):
    """This run's corrected view of a model's window, when the guard has shrunk it: every
    turn re-picks the ref from the registry (which still carries the catalog's figure), so
    the correction is re-applied here rather than by mutating shared registry state.
    """
    overrides = getattr(loop, "_window_overrides", None)
    shrunk = overrides.get((ref.endpoint, ref.model)) if overrides else None
    if shrunk and shrunk < ref.context_chars:
        return dataclasses.replace(ref, context_chars=shrunk)
    return ref


def _shrink_window_to_provider(loop, endpoint, ref, exc: EndpointError) -> tuple | None:
    """Net 0 of _recover_transport: a context-overflow failure whose stated maximum is
    SMALLER than the configured window means the catalog entry lies — shrink the run-local
    window to the provider's figure, re-clamp the prompt under it, emit the audit trail
    (transcript event + `model_window_corrected` health event naming the bad entry), and
    hand back the same endpoint with the corrected ref for one clean retry. Returns None
    when the error is not an overflow, states no figure, config was not the problem, or
    this model was already corrected once this run (never loops).
    """
    stated = parse_overflow_limit(str(exc))
    if stated is None:
        return None
    corrected = int(stated * CHARS_PER_TOKEN)
    key = (ref.endpoint, ref.model)
    overrides = getattr(loop, "_window_overrides", None)
    if overrides is None:
        overrides = loop._window_overrides = {}
    if overrides.get(key, float("inf")) <= corrected or corrected >= ref.context_chars:
        return None
    overrides[key] = corrected
    new_ref = dataclasses.replace(ref, context_chars=corrected)
    cl = clamp_to_cap(loop.messages, new_ref.context_chars, new_ref.max_tokens)
    ctx = loop.ctx
    ctx.transcript.event("compaction", {"window_guard": {
        "model": ref.name or ref.model, "configured_chars": ref.context_chars,
        "provider_max_tokens": stated, "corrected_chars": corrected,
        **({"clamp": cl} if cl else {})}})
    log_health_event(ctx.server.routines_home, "model_window_corrected",
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=(f"{ref.name or ref.model}: catalog claims "
                             f"{ref.context_chars:,} context chars but the provider "
                             f"enforces {stated:,} tokens — run continues on "
                             f"{corrected:,} chars; correct the catalog entry"))
    return endpoint, new_ref


def fold_usage(usage_sum: dict, completion) -> None:
    """Fold one completion's usage into this turn's running sum: in/out, prompt-cache
    traffic (kept out of `in` so token budgets keep their meaning), metered cost, and the
    serving provider — aggregators route per request, and attribution is what lets an
    audit correlate malformed actions with the provider, not the model. The serving MODEL
    itself is stamped by the caller (`usage["model"]`) once an action is accepted, so a
    failed-over or referred turn stays attributable to the model that actually produced it.
    """
    usage_sum["in"] += completion.usage["in"]
    usage_sum["out"] += completion.usage["out"]
    for cache_key in ("cached_in", "cache_write"):
        if completion.usage.get(cache_key):
            usage_sum[cache_key] = usage_sum.get(cache_key, 0) + int(completion.usage[cache_key])
    if completion.usage.get("cost"):
        usage_sum["cost"] = round(usage_sum.get("cost", 0.0) + float(completion.usage["cost"]), 6)
    if completion.provider:
        usage_sum["provider"] = completion.provider


def next_action(loop) -> tuple[dict | None, dict]:
    ctx = loop.ctx
    loop._referred_turn = False   # set when the uncensored model produced THIS turn's action
    chain = ctx.registry.for_model_chain("main", ctx.routine.models)
    endpoint, ref = failover.pick(chain)   # first chain member not in provider cooldown
    ref = _override_window(loop, ref)      # re-apply any run-local window correction (F278)
    ctx.main_model = f"{ref.endpoint}/{ref.model}"     # in status.json; updates on a switch
    compact_if_needed(loop, endpoint, ref)
    usage_sum: dict = {"in": 0, "out": 0}   # + "model"/"provider" attribution (str) on success
    schema = None if loop._schema_off else loop.action_schema
    if loop._shed_schema_turns > 0:
        loop._shed_schema_turns -= 1
        schema = None
    prev_raw: str | None = None
    # Shared across attempts: one uncensored referral per turn (free-text OR
    # classifier-refusal path), and the consecutive-empty streak from the CURRENT model.
    refstate = {"referral_tried": False, "empty": 0}
    base_len = len(loop.messages)   # schema-retry debris beyond this is dropped on success
    attempt = 0
    while attempt < MAX_SCHEMA_ATTEMPTS:
        attempt += 1
        try:
            completion = endpoint.complete(loop.messages, model=ref.model,
                                           schema=schema, effort=ref.effort,
                                           temperature=ref.temperature,
                                           max_tokens=ref.max_tokens,
                                           session=str(ctx.run_dir),
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
            endpoint, ref = _recover_transport(loop, chain, endpoint, ref, exc)
            ctx.main_model = f"{ref.endpoint}/{ref.model}"
            attempt -= 1
            continue
        fold_usage(usage_sum, completion)
        switched = None
        if completion.stop_reason in REFUSAL_STOPS:
            # BEFORE the empty check: a mid-stream classifier cut can leave partial text
            # (and the CLI's refusal envelope carries error prose), but a refused turn is
            # never a usable action and never a same-model retry.
            referred_action, switched = _handle_refusal(loop, completion, chain, ref,
                                                        usage_sum, base_len, attempt,
                                                        refstate)
            if switched is None:
                loop._referred_turn = True      # _handle_refusal raised otherwise
                return referred_action, usage_sum
        elif completion.parsed is None and not completion.text.strip():
            switched = _handle_empty(loop, completion, chain, ref, attempt, refstate)
            if switched is None:
                if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                    schema = None
                # Same test knob as endpoints.base.with_retries: the retry LOGIC always
                # runs, the backoff clock is zeroed in the suite (RSCHED_RETRY_BASE_DELAY).
                time.sleep(1.5 * attempt
                           * float(os.environ.get("RSCHED_RETRY_BASE_DELAY", "1.0")))
                continue
        if switched is not None:
            endpoint, ref = switched
            ctx.main_model = f"{ref.endpoint}/{ref.model}"
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
            if len(loop.messages) > base_len:
                # Drop the failed-attempt/correction pairs from the live prompt — they
                # earned their keep eliciting THIS reply and would otherwise be re-read
                # every remaining turn. The transcript's error events keep the record.
                del loop.messages[base_len:]
            usage_sum["model"] = f"{ref.endpoint}/{ref.model}"   # per-turn attribution
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
            # Refusal referral (opt-in, D8 scope C): a free-text reply that reads as a
            # content refusal — not a malformed action — means the main/subroutine model
            # DECLINED the turn. If the routine configured an `uncensored` model, re-issue
            # this turn to it once; a schema-valid action from it continues the loop
            # untouched. Inert when the role is unset (for_uncensored → None).
            if (not refstate["referral_tried"] and completion.parsed is None
                    and executor._looks_like_refusal(completion.text)):
                refstate["referral_tried"] = True
                referred_action = refer_turn_to_uncensored(loop, usage_sum, base_len)
                if referred_action is not None:
                    loop._referred_turn = True
                    return referred_action, usage_sum
            loop.messages.append({"role": "assistant", "content": raw[:4000]})
            loop.messages.append({"role": "user", "content": retry_message(
                exc.problems, example=KIND_EXAMPLES.get(kind_hint or ""), repeated=repeated)})
            if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                # Persistent violations under a provider-enforced grammar are often the
                # grammar's fault (empty-string debris fields are its signature) — give
                # the final attempt free-form JSON; the contract still demands one object.
                schema = None
    ctx.note_schema_forcefail()
    return None, usage_sum


def _recover_transport(loop, chain, endpoint, ref, exc: EndpointError) -> tuple:
    """The completion call failed with a hard EndpointError — the three runtime nets, in
    order. Net 0: a context-overflow whose stated maximum is smaller than the configured
    window is a lying catalog entry, not a provider failure — shrink the run-local window,
    re-clamp, and retry the SAME model (once; see _shrink_window_to_provider, F278).
    Net 1: a turn whose tail carries an image the endpoint couldn't show is
    converted to vision-util text (media fallback) and retried text-only on the SAME
    model — the cooldown the instrumentation just started is lifted, since the image, not
    the provider, was the problem. Net 2: a genuine hard provider failure advances down
    the role's fallback chain (the failed model is already cooling); chain exhausted →
    re-raise, failing the run exactly as before fallbacks existed. Returns the
    (endpoint, ref) to retry on.
    """
    shrunk = _shrink_window_to_provider(loop, endpoint, ref, exc)
    if shrunk is not None:
        failover.clear(ref.endpoint, ref.model)   # the CONFIG, not the provider, was at fault
        return shrunk
    if apply_media_fallback(loop, exc):
        failover.clear(ref.endpoint, ref.model)
        return endpoint, ref
    switched = _switch_to_fallback(loop, chain, ref, exc)
    if switched is None:
        raise exc
    return switched


def _handle_refusal(loop, completion, chain, ref, usage_sum: dict, base_len: int,
                    attempt: int, refstate: dict) -> tuple[dict | None, tuple | None]:
    """A classifier refusal (stop_reason in REFUSAL_STOPS — an HTTP 200, so error-rate
    monitoring never sees it; content usually empty, stop_details naming the category).
    Never blind-retried against the same model: re-sending a refused prompt usually earns
    another refusal, so the pre-R5 path — 3 same-model retries, then a "failed to produce
    a valid action" death — burned the run while hiding the cause. Instead: a distinct
    refusal-marked transcript error first, then the turn is referred to the `uncensored`
    model once (when configured), else the fallback chain advances — cooling the refused
    model like a hard failure, which is RUN-scoped (the failover registry is
    process-local), so the rest of this run stops re-asking while other runs, with other
    prompts, still probe it fresh. Chain exhausted (or no fallbacks): raises, failing the
    run HONESTLY with the category named — never "empty completion".
    Returns (referred_action, switched_chain_entry) — exactly one is set.
    """
    details = completion.stop_details or {}
    category = str(details.get("category") or "") or "unreported"
    explanation = str(details.get("explanation") or "")
    what = (f"{ref.name or ref.model} refused the turn "
            f"(stop_reason={completion.stop_reason}, category={category}"
            + (f": {explanation[:200]}" if explanation else "") + ")")
    loop.ctx.transcript.event("error", {
        "where": "endpoint", "attempt": attempt, "message": what,
        "refusal": {"category": category, "model": ref.name or ref.model,
                    **({"explanation": explanation[:500]} if explanation else {})}})
    if not refstate["referral_tried"]:
        refstate["referral_tried"] = True
        referred = refer_turn_to_uncensored(loop, usage_sum, base_len)
        if referred is not None:
            return referred, None
    switched = _switch_to_fallback(loop, chain, ref, EndpointError(what))
    if switched is not None:
        return None, switched
    raise EndpointError(
        f"{what}; no usable fallback model — configure `fallbacks:` on the catalog "
        f"model (or an `uncensored` role) to survive classifier refusals")


def _handle_empty(loop, completion, chain, ref,
                  attempt: int, refstate: dict) -> tuple | None:
    """One empty completion (no content, no parsed object) that is NOT a refusal — those
    divert to _handle_refusal before this. A provider hiccup gets a clean same-model retry
    (no poisoned context), but a hard-broken model keeps failing the same way, so the
    SECOND empty in one turn engages the failover chain exactly like a hard EndpointError
    (same-model blind retries can never fix it). Returns the switched chain entry, or
    None = plain same-model retry.
    """
    stop = completion.stop_reason
    # stop_details rides along VERBATIM so the transcript shows WHY the reply was
    # empty (F164) — e.g. a reasoning model that spent its whole budget thinking.
    details = getattr(completion, "stop_details", None) or {}
    loop.ctx.transcript.event("error", {
        "where": "endpoint", "attempt": attempt,
        "message": "empty completion (no content/reasoning; "
                   f"stop_reason={stop or 'unreported'}"
                   + (f", stop_details={details}" if details else "") + ")"})
    refstate["empty"] += 1
    if refstate["empty"] >= 2:
        failure = EndpointError(
            f"empty completion x{refstate['empty']} (stop_reason={stop or 'unreported'})")
        switched = _switch_to_fallback(loop, chain, ref, failure)
        if switched is not None:
            refstate["empty"] = 0
            return switched
        # chain exhausted (or none configured): the caller keeps the pre-failover
        # behavior — remaining attempts run schema-free
    return None


def _switch_to_fallback(loop, chain, failed_ref, exc: EndpointError):
    """The picked model failed hard mid-turn (its adapter's transport retries are already
    exhausted, or it kept returning empty completions). Advance to the next chain member
    not in cooldown and log the switch VISIBLY: a transcript `error` event whose
    `failover` payload names both models — so the run records which model serves from here
    (status.json follows via ctx.main_model). None = chain exhausted.

    The abandoned model is marked cooling HERE — the engine's own judgment that it failed
    a real turn. (InstrumentedEndpoint only cools retryable-class transport failures; a
    deterministic error or an empty-reply pattern is visible only at this seam.)
    """
    failover.mark_failed(failed_ref.endpoint, failed_ref.model)
    nxt = failover.next_after(chain, failed_ref)
    if nxt is None:
        return None
    _, n_ref = nxt
    loop.ctx.transcript.event("error", {
        "where": "endpoint",
        "message": (f"{failed_ref.name or failed_ref.model} failed hard: {str(exc)[:300]} "
                    f"— failing over to {n_ref.name or n_ref.model}"),
        "failover": {"from": failed_ref.name, "to": n_ref.name,
                     "cooldown_s": failover.COOLDOWN_S}})
    return nxt


def refer_turn_to_uncensored(loop, usage_sum: dict, base_len: int) -> dict | None:
    """D8 scope C: the routine's main/subroutine model refused the turn in free text. If an
    `uncensored` model is configured, re-issue the CURRENT turn to it once and return a
    schema-valid action if it produces one (else None → fall back to normal schema retry).
    Opt-in and inert: no `uncensored` role (for_uncensored → None) means no-op. Usage from the
    referred completion is folded into this turn's usage; on success the schema-retry debris
    is dropped like the primary success path. Best-effort — any endpoint/parse failure returns
    None so the loop keeps its existing retry behaviour.
    """
    ctx = loop.ctx
    target = ctx.registry.for_uncensored(ctx.routine.models)
    if target is None:
        return None
    u_endpoint, u_ref = target
    try:
        completion = u_endpoint.complete(loop.messages, model=u_ref.model,
                                         schema=loop.action_schema, effort=u_ref.effort,
                                         temperature=u_ref.temperature,
                                         max_tokens=u_ref.max_tokens,
                                         session=str(ctx.run_dir),
                                         purpose=f"turn {ctx.turn + 1} · referred", kind="turn")
    except EndpointError:
        return None
    fold_usage(usage_sum, completion)
    try:
        candidate, problems = action_candidate(loop, completion)
    except Exception:   # best-effort: a bad referred reply just falls through to normal retry
        return None
    if problems:
        return None
    if len(loop.messages) > base_len:
        del loop.messages[base_len:]
    ctx.referrals += 1
    usage_sum["model"] = f"{u_ref.endpoint}/{u_ref.model}"   # the model that served the turn
    return candidate


def action_candidate(loop, completion) -> tuple[dict, list]:
    """Parse a completion into a normalized action candidate plus validation problems
    (schema first, then per-kind/permission checks). Raises on unparseable text —
    callers decide whether that is a retry or a silent fallback.
    """
    from .interact import recreate_denial  # function-level: interact pulls in the ask stack
    from .requests import request_denial

    candidate = (completion.parsed if completion.parsed is not None
                 else extract_json(completion.text))
    candidate = normalize_action(candidate)
    problems = (validate(candidate, ACTION_SCHEMA)
                or validate_action(candidate, allowed_kinds=loop.allowed_tools,
                                   grants=loop.grants)
                or recreate_denial(loop, candidate)
                or request_denial(loop, candidate))
    if problems and isinstance(candidate, dict):
        # per-util telemetry: a denied/malformed util call never reaches the executor —
        # this validation seam is the only place it can be counted (util_stats)
        counted = util_rejection_outcome(candidate, allowed_kinds=loop.allowed_tools,
                                         grants=loop.grants)
        if counted is not None:
            loop.ctx.count_util(*counted)
    return candidate, problems


def compact_if_needed(loop, endpoint, ref) -> None:
    """Keep the next prompt inside the model's window. First ARCHIVE the middle if it has grown
    past the compaction gate (`_archive_if_needed`), then ENFORCE the hard window ceiling as a
    last resort (`clamp_to_cap`) — because archiving cannot shrink the incompressible head+tail
    floor and a short conversation has no middle to elide, so a run with a few very large
    observations would otherwise 400 with context_length_exceeded and die (F265, three
    recurrences on c-20260802-110156). The clamp trims oversized bodies in place with a visible
    marker; the full text stays in the transcript.
    """
    _archive_if_needed(loop, endpoint, ref)
    ctx = loop.ctx
    cl = clamp_to_cap(loop.messages, ref.context_chars, ref.max_tokens)
    if cl:
        ctx.transcript.event("compaction", {"clamp": cl})
        loop._last_compact_after = messages_size(loop.messages)


def _archive_if_needed(loop, endpoint, ref) -> None:
    """When the prompt exceeds the compaction gate, archive the middle to a navigable on-disk
    history via the LLM (compact_to_history); fall back to the deterministic one-line digest if
    that fails, so a run never stalls on compaction. Does NOT guarantee the result clears the
    window — that is the caller's `clamp_to_cap` step (the head+tail floor is incompressible).
    """
    ctx = loop.ctx
    size = messages_size(loop.messages)
    # Observed cache hits flip the economics: re-reading carried context costs ~0.1x,
    # while compacting rewrites the prefix and invalidates the whole cache — so compact
    # later (0.8) once the provider demonstrably serves from cache, earlier (0.6) when
    # every turn re-reads at full price. The cap also reserves room for the model's
    # OUTPUT (ref.max_tokens): the provider counts prompt + requested output against ONE
    # window, so a small-window model must compact before input + max_tokens overflows it
    # (F265). Both use the MODEL's window, not the endpoint default.
    context_cap = input_cap_chars(ref.context_chars, ref.max_tokens,
                                  cached=bool(ctx.usage.get("cached_in")))
    # Long prompts also burn the token BUDGET — every turn re-sends everything, so a
    # bloated prompt taxes each remaining turn. Once the prompt would eat >10% of the
    # remaining token budget per turn, archive it: the one compaction call costs what
    # the bloat would keep costing every single turn. Floored so a small prompt near
    # budget exhaustion doesn't thrash (compaction itself spends tokens).
    remaining = ctx.tokens_remaining()   # None = unlimited → only the context cap applies
    budget_cap = (float("inf") if remaining is None
                  else max(40_000.0, 0.10 * 4 * remaining))
    if (size <= min(context_cap, budget_cap)
            or len(loop.messages) <= KEEP_HEAD_MSGS + KEEP_TAIL_MSGS):
        return
    # Anti-thrash: head + tail are an incompressible floor (large observations in the last
    # 24 messages stay verbatim), so once the middle is a handful of messages — or the size
    # hasn't grown meaningfully since the last archive — another pass can't win. Each
    # attempt costs a full-prompt LLM call; wait until there is enough new middle to pay
    # for one. (Seen live: 4 compactions in one run, the last archiving 3 messages for a
    # 5k-char gain.)
    middle_n = len(loop.messages) - KEEP_HEAD_MSGS - KEEP_TAIL_MSGS
    if middle_n < 8 or size < loop._last_compact_after + 20_000:
        return
    # Archival is machine work — route it to the (usually cheaper) tool-call model
    # whenever its window can hold the middle being archived; the main model is the
    # fallback, never the default.
    c_endpoint, c_ref = endpoint, ref
    try:
        t_endpoint, t_ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        middle_size = messages_size(
            loop.messages[KEEP_HEAD_MSGS:len(loop.messages) - KEEP_TAIL_MSGS])
        if t_ref.context_chars * 0.7 >= middle_size:
            c_endpoint, c_ref = t_endpoint, t_ref
    except Exception:
        pass
    cinfo = None
    try:
        result = compact_to_history(loop.messages, loop.turn_records, c_endpoint, c_ref,
                                    ctx.run_dir, loop._hist_rel)
    except Exception as exc:
        ctx.transcript.event("error", {"where": "compaction", "message": str(exc)[:300]})
        result = None
    if result is not None:
        loop.messages, cinfo = result
        loop._history_active = True
        loop._hist_note_countdown = 0   # the next observation carries the history pointer
    else:
        loop.messages, cinfo = maybe_compact(loop.messages, loop.turn_records,
                                             ref.context_chars)
    if cinfo:
        if cinfo.get("usage"):
            ctx.add_usage(cinfo["usage"])   # the archival call itself now hits the books
        loop._last_compact_after = messages_size(loop.messages)
        ctx.transcript.event("compaction", cinfo)


def apply_media_fallback(loop, exc: EndpointError) -> bool:
    """The main endpoint failed on a turn whose tail user message carries image `media`
    (it rejected the file, or claude-cli's stream-json path is unavailable). Convert that
    media to vision-util text IN PLACE and drop it, so the retried completion is text-only
    and the model still gets the content. False when the tail has no media — then the
    failure is a genuine endpoint error that must propagate.
    """
    if not loop.messages:
        return False
    last = loop.messages[-1]
    media = last.get("media")
    if not media:
        return False
    notes = []
    for item in media:
        desc = fileops.vision_describe(loop.ctx, item["path"], "")
        notes.append(f"[{Path(item['path']).name}: this run's model could not display it — "
                     f"description from the vision util]\n{desc}")
    last.pop("media", None)
    last["content"] = last["content"] + "\n\n" + "\n\n".join(notes)
    loop.ctx.transcript.event("error", {"where": "media",
        "message": f"main endpoint could not show {len(media)} file(s) "
                   f"({str(exc)[:120]}); fell back to the vision util"})
    return True
