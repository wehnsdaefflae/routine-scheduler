"""One valid action from the model — the completion side of a turn: the schema-guarded
retry cycle (≤3 attempts), repeat-streak schema shedding, refusal referral to the
`uncensored` model, image→vision-util fallback, the prompt-size compaction gate, and
usage folding. Every function takes the live EngineLoop; the turn ORDER stays in loop.run().
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..endpoints.base import EndpointError
from ..schema_guard import SchemaViolation, extract_json, retry_message, validate
from . import executor
from .actions import ACTION_SCHEMA, KIND_EXAMPLES, normalize_action, validate_action
from .history import (
    COMPACT_AT_FRACTION,
    COMPACT_AT_FRACTION_CACHED,
    KEEP_HEAD_MSGS,
    KEEP_TAIL_MSGS,
    compact_to_history,
    maybe_compact,
    messages_size,
)

MAX_SCHEMA_ATTEMPTS = 3   # 1 initial + 2 retries per turn


def fold_usage(usage_sum: dict, completion) -> None:
    """Fold one completion's usage into this turn's running sum: in/out, prompt-cache
    traffic (kept out of `in` so token budgets keep their meaning), metered cost, and the
    serving provider — aggregators route per request, and attribution is what lets an
    audit correlate malformed actions with the provider, not the model.
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
    endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
    ctx.main_model = f"{ref.endpoint}/{ref.model}"     # in status.json; updates on a switch
    compact_if_needed(loop, endpoint, ref)
    usage_sum = {"in": 0, "out": 0}
    schema = None if loop._schema_off else ACTION_SCHEMA
    if loop._shed_schema_turns > 0:
        loop._shed_schema_turns -= 1
        schema = None
    prev_raw: str | None = None
    referral_tried = False   # refer a free-text refusal to the uncensored model once (D8 C)
    base_len = len(loop.messages)   # schema-retry debris beyond this is dropped on success
    for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
        # Generous output cap: reasoning models need room to think AND answer — a
        # provider's small default can swallow the content entirely.
        try:
            completion = endpoint.complete(loop.messages, model=ref.model,
                                           schema=schema, effort=ref.effort,
                                           temperature=ref.temperature,
                                           max_tokens=16_384,
                                           session=str(ctx.run_dir),
                                           # bookkeeping only — the wrapper consumes
                                           # these; they never reach the transport, so
                                           # the prompt is untouched
                                           purpose=f"turn {ctx.turn + 1}"
                                                   + ("" if attempt == 1
                                                      else f" · retry {attempt}"),
                                           kind="turn")
        except EndpointError as exc:
            # Runtime net: if the failure is on a turn whose tail carries an image the
            # endpoint couldn't show, convert it to vision-util text and retry text-only;
            # otherwise it's a real endpoint failure — let it propagate.
            if apply_media_fallback(loop, exc):
                continue
            raise
        fold_usage(usage_sum, completion)
        if completion.parsed is None and not completion.text.strip():
            # Empty reply = provider hiccup, not a model mistake: retry cleanly (no
            # poisoned context); the last attempt drops the provider-side format
            # constraint entirely — the contract in the system prompt still demands JSON.
            ctx.transcript.event("error", {
                "where": "endpoint", "attempt": attempt,
                "message": "empty completion (no content/reasoning)"})
            if attempt == MAX_SCHEMA_ATTEMPTS - 1:
                schema = None
            time.sleep(1.5 * attempt)
            continue
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
            if (not referral_tried and completion.parsed is None
                    and executor._looks_like_refusal(completion.text)):
                referral_tried = True
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
                                         schema=ACTION_SCHEMA, effort=u_ref.effort,
                                         temperature=u_ref.temperature,
                                         max_tokens=16_384, session=str(ctx.run_dir),
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
    return candidate


def action_candidate(loop, completion) -> tuple[dict, list]:
    """Parse a completion into a normalized action candidate plus validation problems
    (schema first, then per-kind/permission checks). Raises on unparseable text —
    callers decide whether that is a retry or a silent fallback.
    """
    candidate = (completion.parsed if completion.parsed is not None
                 else extract_json(completion.text))
    candidate = normalize_action(candidate)
    problems = (validate(candidate, ACTION_SCHEMA)
                or validate_action(candidate, allowed_kinds=loop.allowed_tools,
                                   grants=loop.grants))
    return candidate, problems


def compact_if_needed(loop, endpoint, ref) -> None:
    """When the prompt exceeds ~60% of context, archive the middle to a navigable on-disk
    history via the LLM (compact_to_history); fall back to the deterministic one-line digest if
    that fails, so a run never stalls on compaction.
    """
    ctx = loop.ctx
    size = messages_size(loop.messages)
    # Observed cache hits flip the economics: re-reading carried context costs ~0.1x,
    # while compacting rewrites the prefix and invalidates the whole cache — so compact
    # later (0.8) once the provider demonstrably serves from cache, earlier (0.6) when
    # every turn re-reads at full price.
    fraction = (COMPACT_AT_FRACTION_CACHED if ctx.usage.get("cached_in")
                else COMPACT_AT_FRACTION)
    context_cap = fraction * ref.context_chars   # the MODEL's window, not the endpoint default
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
        desc = executor.vision_describe(loop.ctx, item["path"], "")
        notes.append(f"[{Path(item['path']).name}: this run's model could not display it — "
                     f"description from the vision util]\n{desc}")
    last.pop("media", None)
    last["content"] = last["content"] + "\n\n" + "\n\n".join(notes)
    loop.ctx.transcript.event("error", {"where": "media",
        "message": f"main endpoint could not show {len(media)} file(s) "
                   f"({str(exc)[:120]}); fell back to the vision util"})
    return True
