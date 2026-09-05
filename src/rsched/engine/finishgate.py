"""The finish gate — the guards a run's own `finish` has to pass."""

from __future__ import annotations

from ..ids import now_iso
from . import inbox
from .control import drain_injections
from .finish_guard import unbacked_action_claims


def check_finish(loop, action: dict, ctx) -> str | None:
    # LADDER of guards: each rung is its own teaching deferral, and merging them would
    # make the reasons interchangeable at exactly the moment the model needs the specific
    # one.
    """May this run END? Returns the run status when the finish stands, None when it is
    set aside for one turn (the R108 deferral shape) and the loop should go round again.

    Split out of `EngineLoop.run` (F393). Five guards, one question: an undrained user
    message, unaccounted stopping conditions, a fabricated first-action finish, an
    unbacked action claim, and (F334 v2) a claim the run's own transcript does not
    support. Each costs one turn and says exactly why — the engine never ends a run the
    model could have ended itself, so every rung hands the turn back rather than
    force-finishing.
    """
    # R108/F268: a user message that landed in the window between this
    # turn's inbox drain and the finish is DELIVERED, never silently
    # outlived. The finish is set aside (a rejected observation, like the
    # guards below) and the drained message(s) follow it, so the model
    # addresses them and finishes again. The spent reserved-finish turn is
    # the one exception — deferring it would force-finish the run with an
    # engine string on the next boundary — so _finish_run surfaces the
    # still-queued message to both sides instead.
    if (ctx.depth == 0 and not loop._finish_reserved
            and inbox.has_pending_messages(ctx.routine.dir,
                                           vias=inbox.LIVE_MESSAGE_VIAS)):
        obs = {"kind": "finish", "rejected": True, "pending_user_input": True}
        ctx.transcript.event("observation", obs, turn=ctx.turn)
        loop.messages.append({"role": "user", "content":
            "OBSERVATION (finish deferred): a user message arrived while "
            "you were finishing — it is delivered below instead of being "
            "dropped. Address it, then finish again with an updated "
            "summary."})
        drain_injections(loop)
        ctx.write_status()
        return None   # deferred — the loop goes round again
    # F334/D98 v1: a finish that ignores the user's OPEN stopping
    # conditions is set aside (same one-extra-turn shape as R108 above).
    # The engine checks only the ACCOUNTING — a `[s<n>]` mention per open
    # condition — never the semantics; the reserved-finish turn is exempt
    # (deferring it would force-finish with an engine string).
    if ctx.depth == 0 and not loop._finish_reserved:
        from . import stopping
        if missing := stopping.unaccounted(
                str(action.get("summary") or ""), ctx.routine.dir,
                phase=ctx.phase):
            obs = {"kind": "finish", "rejected": True,
                   "stopping_unaccounted": missing}
            ctx.transcript.event("observation", obs, turn=ctx.turn)
            loop.messages.append({"role": "user", "content":
                "OBSERVATION (finish deferred): your summary does not "
                "account for the open STOPPING CONDITIONS "
                f"{', '.join(missing)} (see the STOPPING CONDITIONS "
                "section). Add a line `[s<n>] met — <evidence>` or "
                "`[s<n>] unmet — <why>` for each, then finish again."})
            ctx.write_status()
            return None   # deferred — the loop goes round again
    if action["status"] == "ok" and loop.executed_actions == 0 and ctx.depth == 0:
        # Fabrication guard: a top-level ok-finish as the very first action
        # is a hallucinated completion (the classic no-tools failure mode) —
        # no observation exists that could ground any of its claims.
        obs = {"kind": "finish", "rejected": True}
        ctx.transcript.event("observation", obs, turn=ctx.turn)
        loop.messages.append({"role": "user", "content":
            "OBSERVATION (finish REJECTED): you have not executed a single "
            "action this run, so the workflow cannot be complete and none of "
            "your claims have observations behind them. Start at workflow "
            "step 1 and do the actual work, one action per turn."})
        ctx.write_status()
        return None   # deferred — the loop goes round again
    if action["status"] == "ok" and ctx.depth == 0:
        # Claim guard (D31=B): a top-level ok-finish whose summary claims a
        # high-signal action (report/ask_user/schedule_run) the run never
        # took is narrated unperformed work — reject so the run either takes
        # the action or drops the claim. Meta routines are exempt (they quote
        # other runs' actions); see finish_guard.py.
        unbacked = unbacked_action_claims(
            action.get("summary", ""),
            {r["kind"] for r in loop.turn_records},
            is_meta="meta" in (ctx.routine.tags or []))
        if unbacked:
            obs = {"kind": "finish", "rejected": True,
                   "unbacked_claims": unbacked}
            ctx.transcript.event("observation", obs, turn=ctx.turn)
            names = ", ".join(unbacked)
            loop.messages.append({"role": "user", "content":
                f"OBSERVATION (finish REJECTED): your summary states you "
                f"performed {names}, but no such action was taken this run. "
                f"Either actually take the action now, or remove that claim "
                f"from your summary, then finish again."})
            ctx.write_status()
            return None   # deferred — the loop goes round again
    # F334/D98 v2: v1 proves the run ACCOUNTED for its conditions, not that
    # the account is true. A second model checks each `met` claim against the
    # run's own transcript. Fail-open at every level, and at most ONE objection
    # per condition per run: the model keeps the last word (a judge that could
    # veto forever would hang the run, which is the outcome conditions exist to
    # replace) and the disagreement is recorded instead.
    disputes: dict[str, str] = {}
    if ctx.depth == 0 and not loop._finish_reserved:
        from . import verifier
        objections = verifier.refuted(loop, str(action.get("summary") or ""))
        fresh = [o for o in objections if o["id"] not in loop._challenged]
        if fresh:
            loop._challenged.update(o["id"] for o in fresh)
            obs = {"kind": "finish", "rejected": True,
                   "stopping_unsupported": [o["id"] for o in fresh]}
            ctx.transcript.event("observation", obs, turn=ctx.turn)
            loop.messages.append({"role": "user",
                                  "content": verifier.challenge_message(fresh)})
            ctx.write_status()
            return None   # deferred — the loop goes round again
        disputes = {o["id"]: o["evidence"] for o in objections}
    loop.final_summary = action["summary"]
    # F334/D98: stamp the model's own [s<n>] met/unmet accounting back into
    # the store. Without this a condition sat at `open` however often a run
    # reported it met, so every reader — the panel, the next run, the user —
    # saw a stale list. Depth 0 only (a child accounts for nothing) and
    # best-effort: a store write must never turn a finished run into a
    # failed one.
    if ctx.depth == 0:
        from . import stopping
        try:
            newly = stopping.record_accounting(
                ctx.routine.dir, action["summary"],
                run_id=ctx.run_id, now=now_iso(), disputes=disputes)
        except OSError as exc:
            ctx.transcript.event(
                "error", {"where": "stopping.record_accounting",
                          "error": str(exc)})
            newly = []
        # `met` is the GOAL ids that newly transitioned — the ones that can retire a routine.
        # `judged` is every verdict this run wrote, goal and run bound alike: a run bound never
        # transitions, so without this the whole per-run accounting would leave no event at all.
        judged = {cid: state for cid, (state, _note)
                  in stopping.read_accounting(action["summary"]).items()}
        if newly or disputes or judged:
            ctx.transcript.event("stopping_update",
                                 {"met": newly, "judged": judged, "run_id": ctx.run_id,
                                  **({"disputed": sorted(disputes)}
                                     if disputes else {})})
        # A newly-met GOAL condition may be the one that finishes the ROUTINE. Only worth asking
        # when something goal-shaped just landed — `record_accounting` returns goal ids only.
        if newly:
            from . import goalreached
            goalreached.maybe_propose_retirement(ctx)
    return loop._finish_run(action["status"], action["summary"], authored=True,
                            reply_to=action.get("reply_to"))
