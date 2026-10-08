"""The finish gate — the guards a run's own `finish` has to pass."""

from __future__ import annotations

from ..ids import now_iso
from . import accounting, donewhen, finishline, goals, inbox, runkind, taskops
from .control import drain_injections
from .finish_guard import unbacked_action_claims


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed the claim check's one-objection-per-line ledger (`loop._challenged`) from the
    challenges recorded inside the guard scope (engine/guardscope.py): each is a deferred
    finish, or a goal check left open, naming the lines it objected to
    (`claims_unsupported`). Without it a resumed leg argued a line the run had already been
    asked about, and a re-asserted verdict never stood.
    """
    loop._challenged = set()
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "observation" and payload.get("kind") in ("finish", "goal"):
            loop._challenged.update(str(i) for i in payload.get("claims_unsupported") or [])


def _defer(loop, ctx, message: str, **why) -> None:
    """Set this finish aside for one turn — the shape every rung shares (the R108 deferral).

    The observation records the rung's own keys (`why` — what the console's transcript reads
    to name the rung) AND the message, because the message is what the model read: a resumed
    leg replays the transcript through `format_observation`, which renders the stored message
    verbatim. Without it the replay showed the bare payload as JSON — the missing accounting
    ids, the refuted claims and the rule's line all gone from the prompt the run resumed with.
    """
    ctx.transcript.event("observation", {"kind": "finish", "rejected": True, **why,
                                         "message": message}, turn=ctx.turn)
    loop.messages.append({"role": "user", "content": message})
    ctx.write_status()


def _owed(loop, ctx, action: dict) -> tuple[list[dict], list[dict]] | None:
    """`(lines, open_outcomes)` a finish must account for — `lines` being the Done-when list
    and the open GOALS (engine/goals.py), both answered met/unmet — or None when it owes
    nothing: a child, a follow-up after a routine run already ended, a conversation reply that
    is not final, or a run with nothing to answer for.

    A routine run whose goals began with the operator's brief owes them in place of its
    Done-when list. A CONVERSATION owes only its goals, and only on a reply it declares
    `final` — the reply cycle's other replies hand back before the job is done, by design.
    """
    lines: list[dict] = []
    outcomes: list[dict] = []
    if ctx.depth > 0:
        pass
    elif runkind.is_conversation(ctx):
        lines = goals.owed(ctx.goals) if action.get("final") is True else []
    elif not getattr(loop, "leg_after_authored", False):
        briefed = any(g.get("source") == "brief" for g in ctx.goals)
        lines = [*([] if briefed else donewhen.read(ctx.routine.dir)), *goals.owed(ctx.goals)]
        outcomes = finishline.open_outcomes(finishline.load(ctx.routine.dir))
    return (lines, outcomes) if lines or outcomes else None


def _claims(verdicts: dict, done: list[dict], outcomes: list[dict]) -> list[dict]:
    """The lines the accounting marks `met`, with what each one says — what the verifier reads.
    An outcome's `met` counts only where the run is its judge; the accounting refused the
    others already.
    """
    lines = [*done, *(o for o in outcomes if o["judge"] == "run")]
    return [{"id": x["id"], "text": x["text"], "stage": x.get("stage", "")}
            for x in lines if verdicts.get(x["id"], ("",))[0] == "met"]


def _undeclared(loop, ctx, action: dict) -> tuple[str, dict] | None:
    """`(message, keys)` for a CONVERSATION reply that leaves a goal open without saying whether
    it is FINAL — the one moment the declaration changes what the finish owes (an open goal is
    met now, or it waits for the next reply). Settled by one field, so like the accounting it
    has no once-only limit; never the reserved turn, for the reason every rung gives.
    """
    still = [g["id"] for g in goals.open_goals(ctx.goals)]
    if loop._finish_reserved or not still or isinstance(action.get("final"), bool):
        return None
    message = (f"OBSERVATION (finish deferred): goals are open ({', '.join(still)}) — declare "
               "whether this reply is FINAL. `final: true` when it delivers what the person "
               "asked, with `accounting` carrying `b<n> met: <evidence>` for each open goal; "
               "`final: false` when you are handing back before that (progress, a question "
               "only they can answer, a blocker) and the goals stay open. Then finish again.")
    return message, {"final_undeclared": True}


def _unanswered(loop, owed: tuple[list[dict], list[dict]] | None,
                verdicts: dict, *, final: bool) -> tuple[str, dict] | None:
    """`(message, keys)` for a finish that still owes work, or None.

    GATED PROCESSING first (engine/taskops.py): a routine that keeps tasks may not end while a
    task due this run has no checkpoint — before the accounting, because the routine's own
    Done when is answered once its tasks are. No once-only limit: one action settles it (a
    checkpoint, `deferred` if the run sets the task aside), so it can never trap a run.

    Then THE ACCOUNTING: one entry per Done-when line of the recipe and per open outcome of the
    finish line, as a FIELD — checked for presence and shape only (semantics stay the model's;
    the verifier reads the `met` claims). The main finish only: a follow-up after the run ended
    answers the person, not the recipe.

    A FINAL conversation reply may not leave a goal unmet: `final` is the claim that what the
    person asked for is delivered, so `accounting.problems` refuses an unmet goal there.

    Both skip the reserved-finish turn (deferring it would force-finish with an engine string).
    """
    if (tasks_owed := taskops.finish_owed(loop)) is not None:
        return tasks_owed, {"tasks_owed": True}
    if owed is not None and not loop._finish_reserved:
        found = accounting.problems(verdicts, *owed, final=final)
        if any(found.values()):
            return accounting.deferral(found), {"accounting": found}
    return None


def check_finish(loop, action: dict, ctx) -> str | None:
    # LADDER of guards: each rung is its own teaching deferral, and merging them would
    # make the reasons interchangeable at exactly the moment the model needs the specific
    # one.
    """May this run END? Returns the run status when the finish stands, None when it is
    set aside for one turn (the R108 deferral shape) and the loop should go round again.

    Split out of `EngineLoop.run` (F393). Nine guards, one question: an undrained user
    message, an ask-back on an approval the finish itself filed, a conversation reply that
    leaves open goals without declaring itself final or not, a due task with no checkpoint
    (gated processing), an incomplete accounting, a
    rule whose moment is the ending itself (`assist.at_finish`), a fabricated first-action
    finish, an unbacked action claim, and a `met` claim the run's own transcript does not
    support. Each costs one turn and says
    exactly why — the engine never ends a run the model could have ended itself, so every
    rung hands the turn back rather than force-finishing.
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
        _defer(loop, ctx,
               "OBSERVATION (finish deferred): a user message arrived while you were "
               "finishing — it is delivered below instead of being dropped. Address it, then "
               "finish again with an updated summary.", pending_user_input=True)
        drain_injections(loop)
        return None   # deferred — the loop goes round again
    # An ASK-BACK on the curated-reminder approval a `remind` op on THIS finish filed
    # (engine/askback.py): the operator asked the run something and is owed the answer now. On
    # any other action their words ride its observation; a finish that STANDS has none, so the
    # run ended with their question unanswered and the approval open. Set aside like the user
    # message above — the reminder note carrying their words rides this deferral
    # (`EngineLoop._finish`). Every ask-back is fresh input, so no once-only limit; never the
    # reserved turn, for the reason every rung here gives.
    if ctx.depth == 0 and not loop._finish_reserved and loop.reminder_asked_back:
        _defer(loop, ctx,
               "OBSERVATION (finish deferred): the user replied to the approval this finish "
               "asked for WITHOUT deciding — their words are in the reminder note below. "
               "Answer them, then finish again carrying the same `remind` op (or a revised "
               "one) — that re-submits the approval.", asked_back=True)
        return None   # deferred — the loop goes round again
    # What the run still OWES before it may end: its due tasks, then its accounting
    # (`_unanswered`). One rung, two questions, asked in that order.
    conversation = ctx.depth == 0 and runkind.is_conversation(ctx)
    owed = _owed(loop, ctx, action)
    verdicts = accounting.parse(action.get("accounting"))
    if (unanswered := (_undeclared(loop, ctx, action) if conversation else None)
            or _unanswered(loop, owed, verdicts, final=conversation)) is not None:
        message, why = unanswered
        _defer(loop, ctx, message, **why)
        return None   # deferred — the loop goes round again
    # A general rule the routine PRACTISES whose moment is the ending itself (a ledger
    # entry not written, a review with no denominator). Same deferral shape as the rungs
    # around it, same two guards — never the reserved turn, never a child — plus its own:
    # `assist.at_finish` allows this at most ONCE per run (per reply, in a conversation), so
    # a rule can ask for the ending to be reconsidered but can never negotiate over it. The
    # deferral names the assists behind it: that record is what keeps "once" across a resume.
    if ctx.depth == 0 and not loop._finish_reserved:
        from . import assist
        if deferral := assist.at_finish(loop, action):
            message, keys = deferral
            _defer(loop, ctx, message, assist=True, assists=keys)
            return None   # deferred — the loop goes round again
    if (action["status"] == "ok" and loop.executed_actions == 0 and ctx.depth == 0
            and not loop._finish_reserved):
        # Fabrication guard: a top-level ok-finish as the very first action
        # is a hallucinated completion (the classic no-tools failure mode) —
        # no observation exists that could ground any of its claims.
        # Exempt on the RESERVED finish turn, like every rung above: rejecting there
        # returns to a loop whose budget is still violated, which force-finishes with an
        # engine string — so the guard costs the run the very summary the reserve exists
        # to author. A reserved turn that reached here executed nothing all run anyway.
        _defer(loop, ctx,
               "OBSERVATION (finish REJECTED): you have not executed a single action this "
               "run, so the workflow cannot be complete and none of your claims have "
               "observations behind them. Start at workflow step 1 and do the actual work, "
               "one action per turn.")
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
        if unbacked and loop._finish_reserved:
            # On the reserved turn the summary is the only thing that survives, and a
            # rejection here loses it to the force-finish. Record the unbacked claim as a
            # note the operator can read instead of refusing the finish over it.
            ctx.transcript.event("error", {"where": "finish",
                "message": "reserved finish turn: summary claims "
                           f"{', '.join(unbacked)} with no such action this run — the "
                           "finish stands because rejecting it would lose the summary"})
        elif unbacked:
            _defer(loop, ctx,
                   f"OBSERVATION (finish REJECTED): your summary states you performed "
                   f"{', '.join(unbacked)}, but no such action was taken this run. Either "
                   "actually take the action now, or remove that claim from your summary, "
                   "then finish again.", unbacked_claims=unbacked)
            return None   # deferred — the loop goes round again
    # The accounting proves each line was ANSWERED, not that the answer is true. Each `met`
    # claim is checked once: deterministically first (a Done-when line whose producing stage
    # this run never entered), then by a second model against the run's own transcript.
    # Fail-open at every level and at most ONE objection per line per run — per reply, in a
    # conversation, and kept across a resume (`rebuild`): the model keeps the last word and
    # the disagreement is recorded instead (engine/verifier.py).
    disputes: dict[str, str] = {}
    if owed is not None and not loop._finish_reserved:
        from . import verifier
        claims = _claims(verdicts, *owed)
        cov = ctx.stage_coverage()
        early = (verifier.unentered(claims, set(cov["entered"])) if cov["declared"] else [])
        blamed = {o["id"] for o in early}
        objections = early + verifier.refuted(
            loop, [c for c in claims if c["id"] not in blamed],
            str(action.get("summary") or ""))
        fresh = [o for o in objections if o["id"] not in loop._challenged]
        if fresh:
            loop._challenged.update(o["id"] for o in fresh)
            ctx.claims_challenged += len(fresh)
            _defer(loop, ctx, verifier.challenge_message(fresh),
                   claims_unsupported=[o["id"] for o in fresh])
            return None   # deferred — the loop goes round again
        disputes = {o["id"]: o["evidence"] for o in objections}
        ctx.claims_disputed += len(disputes)
    loop.final_summary = action["summary"]
    # F521/R1681: the finish STANDS — so say which declared stages this run never entered.
    # Deliberately NOT a rung on the ladder above: skipping a stage is often the right call
    # (a gather stage with nothing to gather), and refusing the finish would teach recipes to
    # read modules they do not need. It is a NOTICE, like the residual an `unmet` condition
    # carries: the run ends, and the gap is on the record for whoever reads it next.
    if ctx.depth == 0:
        coverage = ctx.stage_coverage()
        if coverage["skipped"]:
            ctx.transcript.event("stages_skipped", {**coverage, "run_id": ctx.run_id})
    # The accounting lands where every reader finds it: the run's status (the runs table, the
    # dashboard, the next run's digest), the finish line's distances and `met` outcomes, and
    # one `stopping_update` event. Depth 0 only and best-effort: a store write must never turn
    # a finished run into a failed one.
    if ctx.depth == 0 and verdicts:
        ctx.accounting = [str(e) for e in action.get("accounting") or []]
        newly: list[str] = []
        if outcome_verdicts := {i: v for i, v in verdicts.items() if i.startswith("g")}:
            try:
                newly = finishline.record(ctx.routine.dir, outcome_verdicts, run_id=ctx.run_id,
                                          now=now_iso(), disputes=disputes)
            except OSError as exc:
                ctx.transcript.event("error", {"where": "finishline.record",
                                               "error": str(exc)})
        # the goals' verdicts land on the ledger — only the lines this finish OWED, which are
        # the ones the claim check read: a `met` on a reply that owed nothing (not final, a
        # follow-up) was never checked and closes nothing — and the ledger rides the event:
        # that snapshot is what a later leg replays the goals from (engine/goals.py)
        owed_ids = {x["id"] for x in owed[0]} if owed is not None else set()
        stamped = goals.stamp(ctx.goals, {i: v for i, v in verdicts.items() if i in owed_ids
                                          and i.startswith("b")}, disputes=disputes)
        ctx.transcript.event("stopping_update",
                             {"met": newly, "judged": {i: v for i, (v, _n) in verdicts.items()},
                              "run_id": ctx.run_id,
                              **({"disputed": sorted(disputes)} if disputes else {}),
                              **({"goals": ctx.goals} if stamped else {})})
        if newly:
            from . import goalreached
            goalreached.maybe_propose_retirement(ctx)
    return loop._finish_run(action["status"], action["summary"], authored=True,
                            reply_to=action.get("reply_to"),
                            final=action["final"] if conversation
                            and isinstance(action.get("final"), bool) else None)
