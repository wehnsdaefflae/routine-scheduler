"""How a run ENDS — the engine-authored verdicts, and the close-out every ending shares.

The turn state machine decides THAT a run ends; this says what the ending records. One
close-out whoever ends the run (authored finish, spent reserved turn, abort, transport failure,
fail-fast verdict), so result.md, the autocommit, the health event and the final status cannot
disagree.

The verdict sentences name the right cause, because someone acts on them: the model that failed
AND the one the operator chose, the first failover rung as well as the last, policy apart from
model quality, and for an abort the actor the door recorded (`rsched/abortmark.py`).
"""

from __future__ import annotations

from .. import abortmark
from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import (
    archival,
    artsnap,
    assist,
    background,
    closingasks,
    inbox,
    jobops,
    ledgerentry,
    notereads,
    recipeproposal,
    reviewleg,
    reviseleg,
    runkind,
    turnwindow,
)
from .autocommit import autocommit
from .finish_guard import normalize_escaped_newlines
from .run_context import RunContext

#: Consecutive turns that each needed schema-rejection retries (D87-A): fail early rather than
#: limp through the budget at full-prompt retry prices.
SCHEMA_STORM_TURNS = 4


def _serving_model_phrase(ctx: RunContext) -> str:
    """How a failure verdict should NAME the model it is talking about.

    `ctx.main_model` follows every failover switch, so after a failover the sentence names both
    the serving and the configured model and how far down the chain it got; otherwise the
    short form.
    """
    if ctx.failover_rungs and ctx.configured_model and ctx.main_model != ctx.configured_model:
        rungs = "one rung" if ctx.failover_rungs == 1 else f"{ctx.failover_rungs} rungs"
        return (f"the model now serving this run ({ctx.main_model}, {rungs} down the fallback "
                f"chain from the configured {ctx.configured_model})")
    return f"the model ({ctx.main_model})"


def _model_advice(ctx: RunContext) -> str:
    """The "pick a stronger model" clause — dropped when it would be false advice.

    After a failover the configured model was unreachable rather than weak, so the fix is the
    transport or the chain, never the routine's config.
    """
    if ctx.failover_rungs and ctx.configured_model and ctx.main_model != ctx.configured_model:
        return (f". The configured model was not the one that failed, so a stronger model is "
                f"not the remedy — {ctx.configured_model} was unreachable. Check that "
                f"transport and the fallback chain")
    return " — pick a stronger model"


def no_action_verdict(ctx: RunContext, attempts: int) -> str:
    """A turn that spent every schema attempt without an accepted action."""
    return (f"No action was ACCEPTED in {attempts} attempts "
            f"({ctx.schema_retries} rejections this run, {ctx.turn} completed "
            f"turns). The last rejection in the transcript names the wall: "
            f"schema-invalid output means {_serving_model_phrase(ctx)} cannot "
            f"hold the action schema{_model_advice(ctx)}; repeated "
            "capability/grant denials mean the run was boxed in by policy, "
            "and no model change fixes that (R404/F351).")


def storm_verdict(ctx: RunContext) -> str:
    """D87-A: SCHEMA_STORM_TURNS consecutive turns that each needed schema retries."""
    return (f"Schema storm: every one of the last {SCHEMA_STORM_TURNS} "
            f"turns needed schema-rejection retries ({ctx.schema_retries} "
            f"rejections so far from {ctx.main_model}) — "
            f"{_serving_model_phrase(ctx)} cannot reliably hold the action "
            f"schema; failing early instead of burning the budget on "
            f"retries (D87).{_model_advice(ctx)}")


def endpoint_verdict(ctx: RunContext, exc: EndpointError) -> str:
    """A run its transport ended — and, after a failover walk, what began it.

    After a chain walk the error in hand is the LAST rung's; the first rung's is the actionable
    one, so both are named with the rung count.
    """
    hint = (" Check the endpoint's key file under ~/.credentials/ (see config.yaml)."
            if exc.auth else "")
    if first := ctx.first_failover_cause:
        step = "1 rung" if ctx.failover_rungs == 1 else f"{ctx.failover_rungs} rungs"
        return (f"Endpoint failure: the model chain was exhausted after {step}. "
                f"First: {first}. Last: {exc}.{hint}")
    return f"Endpoint failure: {exc}.{hint}"


def aborted(loop) -> str:
    """Close a run out as `aborted`, NAMING the actor that stopped it (F751).

    A signal carries no sender, so the actor is resolved from what the door recorded BEFORE
    signalling (`rsched/abortmark.py`) and rides the finish event as a field as well as the
    prose. An abort nothing recorded stays `unknown`.
    """
    mark = abortmark.resolve(loop.ctx.root_run_dir, loop.abort_event)
    return finish_run(loop, "aborted", mark.sentence, abort=mark)


def abort_fields(abort: abortmark.Abort) -> dict[str, object]:
    """What an abort files as FIELDS, built once for both surfaces that carry them.

    `actor` (who) and `cause` (the health stream's word for them) make aborts filterable.
    `unseen_s` is how long the abort went unseen between the door's stamp and the engine
    resolving it; it is DROPPED when nothing measured it, since a 0 would claim the engine was
    instant.
    """
    fields: dict[str, object] = {"actor": abort.actor, "cause": abort.cause}
    if abort.unseen_s is not None:
        fields["unseen_s"] = abort.unseen_s
    return fields


def finish_run(loop, status: str, summary: str, *, authored: bool = False,
               reply_to: str | None = None, final: bool | None = None,
               abort: abortmark.Abort | None = None) -> str:
    """Close the run out — every ending, authored or not, through this one path.

    A NARROW revise leg (engine/reviseleg.py) closes out the LEG: `status` is the leg's own and
    rides the finish event as `revise_leg`, while the run keeps the verdict its proposing leg
    recorded — status.json, the returned status (the usage row's), the health stream (no row at
    all) — and `result.md` gains the leg's line instead of losing the run's summary to it.
    """
    ctx = loop.ctx
    leg = reviseleg.active(loop)
    run_status = reviseleg.run_status(loop, status)
    window = (turnwindow.finished_window(ctx, kind="edit" if leg else "task",
                                         status=status, spent=loop._budget_spent)
              if ctx.depth == 0 else None)
    # repair a summary whose newlines were double-escaped (literal ``\n``, no real newline)
    summary = normalize_escaped_newlines(summary)
    archival.settle(loop)   # an archive already in flight gets a moment to land
    # …and a BACKGROUNDED ACTION the run never read: said in the SUMMARY, because the run cannot
    # flag evidence it was never shown. Still running (result lost) and landed-unread are two
    # different sentences (`background_summary.settle_note`); "" when everything was read.
    if unread_note := background.settle(loop):
        summary += "\n" + unread_note
    killed = loop.subruns.kill_all(reason=f"parent run finished ({status})")
    if killed:
        summary += f"\n[{killed} still-running sub-workflow(s) were terminated at run end.]"
    if ctx.depth == 0 and inbox.has_pending_messages(ctx.routine.dir,
                                                     vias=inbox.LIVE_MESSAGE_VIAS):
        # Endings the finish deferral cannot serve (spent reserved turn, abort, engine failure,
        # a racing message): say so in result.md; the message stays queued for the next leg.
        summary += ("\n[A user message arrived as this run ended — it could not be "
                    "delivered this run; it stays queued and opens the next "
                    "run/reply.]")
    if ctx.depth == 0 and not leg:   # a revise leg owes no job and proposes no revision
        # The REVIEW LEG first, before the finish event and the autocommit, so its notes are
        # committed with the run and its recipe revisions join the one card below.
        summary += reviewleg.maybe_run(loop, status, summary, authored=authored,
                                       aborted=abort is not None)
        # job work never checkpointed is carried to the next run (engine/jobops.py)
        summary += jobops.close_out(ctx, status)
        # D200: the summary's recipe revisions become ONE Decisions proposal. Here rather than
        # in the finish gate, because a spent reserved turn never reaches the gate.
        if card := recipeproposal.maybe_propose(ctx, summary):
            summary += ("\n[Recipe revision(s) proposed to the operator — he applies them on "
                        f"the Decisions page ({card}).]")
    finish_payload = {"status": run_status, "summary": summary, "authored": authored,
                      **({"budget_window": window} if window is not None else {})}
    if leg:
        finish_payload["revise_leg"] = {"status": status}
    # D212: the run's open CLOSING asks ride the finish, so they reach the person reading what
    # it did (selection only, engine/closingasks.py; none adds no key).
    if asks := closingasks.for_run(ctx):
        finish_payload["closing_asks"] = asks
    if abort is not None:
        finish_payload.update(abort_fields(abort))
    if reply_to:   # the reply targets an earlier message (conversations)
        finish_payload["reply_to"] = reply_to
    if final is not None:   # a conversation reply's own declaration (engine/goals.py)
        finish_payload["final"] = final
        ctx.final = final
    ctx.transcript.event("finish", finish_payload,
                         usage_total=ctx.usage_total(), turns=ctx.turn)
    if ctx.depth == 0 and not leg:   # the run's ending was filed by the leg that ran it
        _log_ending(loop, status, summary, abort)
    loop.final_summary = loop.final_summary or summary
    if ctx.depth == 0:
        from ..paths import atomic_write
        atomic_write(ctx.run_dir / "result.md",
                     reviseleg.result_text(ctx.run_dir, summary, status) if leg
                     else summary + "\n")
        # A conversation is not versioned, so snapshot its artifacts per reply for ⟲ rewind
        # (engine/artsnap.py; a routine has git). Best-effort: never fails a reply.
        if runkind.is_conversation(ctx):
            artsnap.take(ctx.routine.dir, ctx.run_ts, ctx.turn)
        # P1: the engine writes the LEDGER.md entry, before the autocommit commits it
        ledgerentry.record(loop, status, summary)
        if not leg and recipeproposal.asks_for_revisions(ctx):
            notereads.record(ctx)   # which notes this routine run read, before the autocommit
        # the `run-end` hook point: result written, before the autocommit, so a line left for
        # the next run (`state/notes.md`) is committed with this one
        assist.at_run_end(loop, {"status": status, "killed": killed, "run_id": ctx.run_id})
        autocommit(ctx.routine.dir,   # routines never run git
                   f"{ctx.run_id}: recipe revision ({status})" if leg
                   else f"{ctx.run_id}: {status}",
                   routines_home=ctx.server.routines_home, run_id=ctx.run_id)
        state = {"ok": "finished", "partial": "finished", "failed": "failed",
                 "aborted": "aborted"}.get(run_status, "finished")
        ctx.outcome = run_status   # `state` folds partial into finished — this keeps it visible
        ctx.finished_window = window
        ctx.write_status(state, question=None)
    return run_status


def _log_ending(loop, status: str, summary: str,
                abort: abortmark.Abort | None = None) -> None:
    """The health event an ending files, if any.

    `budget_exhausted` is decided by the spent reserved turn, NOT the status: a partial the
    model chose itself is `run_partial`, and a budget-forced run that still finished `ok` files
    `budget_exhausted` too.

    An `ok` ending also files the CORRECTION for any not-ok ending an earlier leg of this run
    left (`rsched/runcorrection.py`); the originals stay.
    """
    ctx = loop.ctx
    if loop._finish_reserved:
        event_type = "budget_exhausted"
    elif status == "partial":
        event_type = "run_partial"
    elif status in ("failed", "aborted"):
        event_type = "run_failed"
    else:
        _correct_earlier_endings(loop, summary)
        return
    # `resource`/`limit` ride as FIELDS so which budget ended the run is filterable
    spent = loop._budget_spent or {}
    log_health_event(ctx.server.routines_home, event_type,
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=summary[:500],
                     status=status if event_type == "budget_exhausted" else None,
                     resource=spent.get("resource"), limit=spent.get("limit"),
                     # an abort's actor/cause (the reap's vocabulary) and `unseen_s`
                     **(abort_fields(abort) if abort else {}))
    if status == "ok":
        # budget-forced yet ok: the row above stands, and earlier legs are still corrected
        _correct_earlier_endings(loop, summary)


def _correct_earlier_endings(loop, summary: str) -> None:
    """An `ok` ending supersedes any not-ok ending this same run left on the health stream.

    A resume reuses the run_id, so the run's other records self-correct; the append-only health
    stream needs this explicit row.
    """
    from ..runcorrection import record

    ctx = loop.ctx
    record(ctx.server.routines_home, routine=ctx.routine.slug, run_id=ctx.run_id,
           summary=summary[:200])


def exit_commands_only(loop) -> str:
    """A conversation woken ONLY to run slash commands: the commands already executed in
    boot, appending their events to the transcript. End the leg with NO model turn and NO
    authored reply (no finish event, result.md untouched) so the conversation returns to
    idle and the user keeps the speaking turn. The next PROSE message resumes normally and
    the model sees the command results replayed from the transcript.
    """
    loop.ctx.write_status("finished", question=None)
    return "finished"
