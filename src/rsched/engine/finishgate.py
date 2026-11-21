"""The finish gate — the guards a run's own `finish` has to pass."""

from __future__ import annotations

from ..ids import now_iso
from . import (
    accounting,
    assist,
    donewhen,
    finish_rungs,
    finishline,
    goalreached,
    goals,
    inbox,
    jobops,
    reviseleg,
    runkind,
    verifier,
)
from .control import drain_injections
from .finish_guard import unbacked_action_claims

# Every import on the finish path is at module scope and must stay there (F658): a lazy import
# resolves FRESH FROM DISK, so a run that edits `src/rsched/` would reach its finish with an OLD
# caller calling a NEW module — and lose its authored summary to an AttributeError.


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed the claim check's one-objection-per-line ledger (`loop._challenged`) from the
    challenges recorded inside the guard scope (engine/guardscope.py): each is a deferred
    finish, or a goal check left open, naming the lines it objected to
    (`claims_unsupported`), so a resumed leg does not re-argue a line already objected to.
    """
    loop._challenged = set()
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "observation" and payload.get("kind") in ("finish", "goal"):
            loop._challenged.update(str(i) for i in payload.get("claims_unsupported") or [])


def _defer(loop, ctx, message: str, **why) -> None:
    """Set this finish aside for one turn — the shape every rung shares.

    The observation records the rung's own keys (`why`, which names the rung) AND the message,
    because a resumed leg replays the stored message verbatim as what the model read.
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
    if (not finish_rungs.eligible(loop, ctx, "final_undeclared") or not still
            or isinstance(action.get("final"), bool)):
        return None
    message = (f"OBSERVATION (finish deferred): goals are open ({', '.join(still)}) — declare "
               "whether this reply is FINAL. `final: true` when it delivers what the person "
               "asked, with `accounting` carrying `b<n> met: <evidence>` for each open goal; "
               "`final: false` when you are handing back before that (progress, a question "
               "only they can answer, a blocker) and the goals stay open. Then finish again.")
    return message, {"final_undeclared": True}


def _unanswered(loop, ctx, owed: tuple[list[dict], list[dict]] | None,
                verdicts: dict, *, final: bool,
                arrived: bool = True) -> tuple[str, dict] | None:
    """`(message, keys)` for a finish that still owes work, or None.

    GATED PROCESSING first (engine/jobops.py): a routine that keeps jobs may not end while a
    task due this run has no checkpoint — before the accounting, because the routine's own
    Done when is answered once its jobs are. No once-only limit: one action settles it (a
    checkpoint, `deferred` if the run sets the task aside), so it can never trap a run.

    Then THE ACCOUNTING: one entry per Done-when line of the recipe and per open outcome of the
    finish line, as a FIELD — checked for presence, shape and a content FLOOR
    (`accounting.is_placeholder`), never semantics. The main finish only: a follow-up after
    the run ended answers the person, not the recipe.

    PRESENCE is read from the raw field (`arrived`): a field that never arrived parses like one
    full of unparseable entries, and absence gets its own head.

    A FINAL conversation reply may not leave a goal unmet: `final` is the claim that what the
    person asked for is delivered, so `accounting.problems` refuses an unmet goal there.

    Both skip the reserved-finish turn (deferring it would force-finish with an engine string).
    """
    if (finish_rungs.eligible(loop, ctx, "jobs_owed")
            and (jobs_owed := jobops.finish_owed(loop)) is not None):
        return jobs_owed, {"jobs_owed": True}
    if owed is not None and finish_rungs.eligible(loop, ctx, "accounting"):
        found = accounting.problems(verdicts, *owed, final=final, arrived=arrived)
        if any(found.values()):
            return accounting.deferral(found), {"accounting": found}
    return None


def check_finish(loop, action: dict, ctx) -> str | None:
    # LADDER of guards: each rung is its own teaching deferral, never merged.
    """May this run END? Returns the run status when the finish stands, None when it is
    set aside for one turn and the loop should go round again.

    Nine guards, one question: an undrained user
    message, an ask-back on an approval the finish itself filed, a conversation reply that
    leaves open goals without declaring itself final or not, a due task with no checkpoint
    (gated processing), an incomplete accounting, a
    rule whose moment is the ending itself (`assist.at_finish`), a fabricated first-action
    finish, an unbacked action claim, and a `met` claim the run's own transcript does not
    support. Each costs one turn and says
    exactly why — the engine never ends a run the model could have ended itself, so every
    rung hands the turn back rather than force-finishing.
    """
    # A user message that landed between this turn's inbox drain and the finish is DELIVERED,
    # never silently outlived. On the spent reserved-finish turn _finish_run surfaces it instead.
    if (finish_rungs.eligible(loop, ctx, "pending_user_input")
            and inbox.has_pending_messages(ctx.routine.dir,
                                           vias=inbox.LIVE_MESSAGE_VIAS)):
        _defer(loop, ctx,
               "OBSERVATION (finish deferred): a user message arrived while you were "
               "finishing — it is delivered below instead of being dropped. Address it, then "
               "finish again with an updated summary.", pending_user_input=True)
        drain_injections(loop)
        return None   # deferred — the loop goes round again
    # An ASK-BACK on the curated-reminder approval a `remind` op on THIS finish filed
    # (engine/askback.py): a finish that stood would leave the operator's question unanswered.
    # Fresh input each time, so no once-only limit.
    if finish_rungs.eligible(loop, ctx, "asked_back") and loop.reminder_asked_back:
        _defer(loop, ctx,
               "OBSERVATION (finish deferred): the user replied to the approval this finish "
               "asked for WITHOUT deciding — their words are in the reminder note below. "
               "Answer them, then finish again carrying the same `remind` op (or a revised "
               "one) — that re-submits the approval.", asked_back=True)
        return None   # deferred — the loop goes round again
    # What the run still OWES before it may end: its due jobs, then its accounting
    # (`_unanswered`). One rung, two questions, asked in that order.
    conversation = ctx.depth == 0 and runkind.is_conversation(ctx)
    owed = _owed(loop, ctx, action)
    verdicts = accounting.parse(action.get("accounting"))
    if (unanswered := (_undeclared(loop, ctx, action) if conversation else None)
            or _unanswered(loop, ctx, owed, verdicts, final=conversation,
                           arrived=accounting.arrived(action.get("accounting")))) is not None:
        message, why = unanswered
        _defer(loop, ctx, message, **why)
        return None   # deferred — the loop goes round again
    # A general rule whose moment is the ending itself. `assist.at_finish` allows this at most
    # ONCE per guard scope, so a rule can ask but never negotiate; the deferral names the
    # assists behind it, which keeps "once" across a resume.
    if (finish_rungs.eligible(loop, ctx, "assist")
            and (deferral := assist.at_finish(loop, action))):
        message, keys = deferral
        _defer(loop, ctx, message, assist=True, assists=keys)
        return None   # deferred — the loop goes round again
    if (action["status"] == "ok" and loop.executed_actions == 0
            and finish_rungs.eligible(loop, ctx, "no_actions")):
        # Fabrication guard: an ok-finish as the very first action has no observation to
        # ground any claim. Exempt on the RESERVED finish turn, like every rung above.
        _defer(loop, ctx,
               "OBSERVATION (finish REJECTED): you have not executed a single action this "
               "run, so the workflow cannot be complete and none of your claims have "
               "observations behind them. Start at workflow step 1 and do the actual work.")
        return None   # deferred — the loop goes round again
    if action["status"] == "ok" and finish_rungs.eligible(loop, ctx, "unbacked_claims"):
        # Claim guard: a summary claiming a high-signal action the run never took is narrated
        # unperformed work. Meta routines are exempt (they quote other runs' actions).
        unbacked = unbacked_action_claims(
            action.get("summary", ""),
            loop.executed_kinds,     # what EXECUTED, never what was merely emitted
            is_meta="meta" in (ctx.routine.tags or []))
        if unbacked and loop._finish_reserved:
            # On the reserved turn a rejection would lose the summary; record it instead.
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
    # Each `met` claim is checked once against the run's own record (engine/verifier.py). ONE
    # judge reads the evidence; a stage never entered is only a HINT it weighs, since stage
    # entry is an unreliable signal. Fail-open, at most ONE objection per line per guard scope
    # (kept across a resume by `rebuild`): the model keeps the last word, the dispute is recorded.
    disputes: dict[str, str] = {}
    if owed is not None and finish_rungs.eligible(loop, ctx, "claims_unsupported"):
        claims = _claims(verdicts, *owed)
        cov = ctx.stage_coverage()
        if cov["declared"]:
            claims = verifier.hinted(claims, set(cov["entered"]))
        objections = verifier.refuted(loop, claims, str(action.get("summary") or ""))
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
    # The finish STANDS — record which declared stages this run never entered. A NOTICE, not a
    # rung: skipping a stage is often the right call.
    if ctx.depth == 0 and not reviseleg.active(loop):   # a revise leg enters no stage by design
        coverage = ctx.stage_coverage()
        if coverage["skipped"]:
            ctx.transcript.event("stages_skipped", {**coverage, "run_id": ctx.run_id})
    # The accounting lands in the run's status, the finish line and one `stopping_update`
    # event. Depth 0 only and best-effort: a store write never fails a finished run.
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
        # only the goal lines this finish OWED (the ones the claim check read) are stamped; the
        # ledger rides the event, which is what a later leg replays the goals from
        owed_ids = {x["id"] for x in owed[0]} if owed is not None else set()
        stamped = goals.stamp(ctx.goals, {i: v for i, v in verdicts.items() if i in owed_ids
                                          and i.startswith("b")}, disputes=disputes,
                             waits=accounting.waits(action.get("accounting")))
        ctx.transcript.event("stopping_update",
                             {"met": newly, "judged": {i: v for i, (v, _n) in verdicts.items()},
                              "run_id": ctx.run_id,
                              **({"disputed": sorted(disputes)} if disputes else {}),
                              **({"goals": ctx.goals} if stamped else {})})
        if newly:
            goalreached.maybe_propose_retirement(ctx)
    return loop._finish_run(action["status"], action["summary"], authored=True,
                            reply_to=action.get("reply_to"),
                            final=action["final"] if conversation
                            and isinstance(action.get("final"), bool) else None)
