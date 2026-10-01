"""How a run ENDS — the engine-authored verdicts, and the close-out every ending shares.

Split out of `loop.py` (F393): the turn state machine decides THAT a run ends; this says what
the ending records. One close-out whoever ends the run — the model's own finish through the
finish gate, the spent reserved turn, an abort, a transport the chain could not survive, a
fail-fast verdict — so result.md, the autocommit, the health event and the final status cannot
disagree about how a run went.

The verdict sentences live here too, because each one is read AFTER the fact by someone deciding
what to change: they name the model that failed AND the one the operator chose (F547/D146), the
first rung of a failover walk as well as the last (F566), and policy apart from model quality
(R404/F351) — advice that names the wrong cause sends the fix to the wrong place.
"""

from __future__ import annotations

from ..endpoints.base import EndpointError
from ..health_events import log_health_event
from . import archival, inbox
from .autocommit import autocommit
from .finish_guard import normalize_escaped_newlines
from .run_context import RunContext

#: D87-A: consecutive TURNS that each needed schema-rejection retries before landing an
#: action — a model that reliably cannot hold the action schema. Fail early and clearly
#: instead of limping through the budget at full-prompt retry prices (F297/R255:
#: c-20260806-150112 burned 12 retries / 477K input tokens before dying late).
SCHEMA_STORM_TURNS = 4


def _serving_model_phrase(ctx: RunContext) -> str:
    """How a failure verdict should NAME the model it is talking about.

    `ctx.main_model` follows every failover switch, so on a run that stepped down the chain
    these verdicts were indicting a model the operator never chose — while the routine's
    config page, the dashboard row and the operator's own memory all said the primary.
    weightloss:20260923-220004 was configured on Opus high, was served two rungs down after a
    503 and a 402, and reported that "the model cannot reliably hold the action schema"
    naming the third rung (F547/D146).

    So when the serving model is NOT the configured one, the sentence says both and how far
    down the chain it got. When nothing failed over, it stays the short form it always was.
    """
    if ctx.failover_rungs and ctx.configured_model and ctx.main_model != ctx.configured_model:
        rungs = "one rung" if ctx.failover_rungs == 1 else f"{ctx.failover_rungs} rungs"
        return (f"the model now serving this run ({ctx.main_model}, {rungs} down the fallback "
                f"chain from the configured {ctx.configured_model})")
    return f"the model ({ctx.main_model})"


def _model_advice(ctx: RunContext) -> str:
    """The "pick a stronger model" clause — dropped when it would be false advice.

    Telling the operator to choose a stronger model is actionable only when the model that
    failed is the model they chose. After a failover it is the opposite of useful: the
    configured model IS the strong one and it was unreachable, so the fix is the transport or
    the chain, never the routine's config (F547/D146).
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

    F566: when the run walked down a chain to get here, the error in hand is the LAST rung's —
    and the first rung's is the one a reader can act on. Name both, with the rung count, so the
    line says what started the collapse and what ended it.
    """
    hint = (" Check the endpoint's key file under ~/.credentials/ (see config.yaml)."
            if exc.auth else "")
    if first := ctx.first_failover_cause:
        step = "1 rung" if ctx.failover_rungs == 1 else f"{ctx.failover_rungs} rungs"
        return (f"Endpoint failure: the model chain was exhausted after {step}. "
                f"First: {first}. Last: {exc}.{hint}")
    return f"Endpoint failure: {exc}.{hint}"


def finish_run(loop, status: str, summary: str, *, authored: bool = False,
               reply_to: str | None = None) -> str:
    """Close the run out — every ending, authored or not, through this one path."""
    ctx = loop.ctx
    # R82: repair a summary whose newlines were double-escaped (literal ``\n`` and no real
    # newline) so result.md / the digest render real line breaks instead of verbatim "\n".
    summary = normalize_escaped_newlines(summary)
    archival.settle(loop)   # an archive already in flight gets a moment to land
    killed = loop.subruns.kill_all(reason=f"parent run finished ({status})")
    if killed:
        summary += f"\n[{killed} still-running sub-workflow(s) were terminated at run end.]"
    if ctx.depth == 0 and inbox.has_pending_messages(ctx.routine.dir,
                                                     vias=inbox.LIVE_MESSAGE_VIAS):
        # The paths the R108 deferral cannot serve (the spent reserved-finish turn,
        # aborts, engine failures — plus a message racing this very write): the
        # message could not become a turn THIS run, so say so on BOTH sides — this
        # note rides result.md (a conversation's rendered reply) and the next run's
        # digest. The message itself stays queued; the next leg's boot drains it.
        summary += ("\n[A user message arrived as this run ended — it could not be "
                    "delivered this run; it stays queued and opens the next "
                    "run/reply.]")
    finish_payload = {"status": status, "summary": summary, "authored": authored}
    if reply_to:   # F438/D117: the reply targets an earlier message (conversations)
        finish_payload["reply_to"] = reply_to
    ctx.transcript.event("finish", finish_payload,
                         usage_total=ctx.usage_total(), turns=ctx.turn)
    if ctx.depth == 0:
        _log_ending(loop, status, summary)
    loop.final_summary = loop.final_summary or summary
    if ctx.depth == 0:
        from ..paths import atomic_write
        atomic_write(ctx.run_dir / "result.md", summary + "\n")
        autocommit(ctx.routine.dir, f"{ctx.run_id}: {status}",   # routines never run git
                   routines_home=ctx.server.routines_home, run_id=ctx.run_id)
        state = {"ok": "finished", "partial": "finished", "failed": "failed",
                 "aborted": "aborted"}.get(status, "finished")
        ctx.outcome = status   # `state` folds partial into finished — this keeps it visible
        ctx.write_status(state, question=None)
    return status


def _log_ending(loop, status: str, summary: str) -> None:
    """The health event an ending files, if any.

    A `partial` is budget_exhausted ONLY when a budget violation forced it (the reserved
    finish turn was spent, or the engine ended it). A partial the model chose on its own — the
    job needs another run, an ask timed out, a source was down — is run_partial: every routine
    in the fleet was reading as "out of budget" while 2 of 3 such finishes were authored with
    budget to spare.

    The reserved turn is what decides, NOT the status: a run that spends it and still finishes
    `ok` was ended by a budget just as much, and used to leave no event at all — 11 such runs
    since 09-01, so the fleet's budget-forced endings read as a smaller number than they are.
    """
    ctx = loop.ctx
    if loop._finish_reserved:
        event_type = "budget_exhausted"
    elif status == "partial":
        event_type = "run_partial"
    elif status in ("failed", "aborted"):
        event_type = "run_failed"
    else:
        return
    # `resource` and `limit` ride as FIELDS, not prose: which budget ended the run is exactly
    # the question a sweep must be able to filter on, and `detail` carried only the summary.
    spent = loop._budget_spent or {}
    log_health_event(ctx.server.routines_home, event_type,
                     routine=ctx.routine.slug, run_id=ctx.run_id,
                     detail=summary[:500],
                     status=status if event_type == "budget_exhausted" else None,
                     resource=spent.get("resource"), limit=spent.get("limit"))


def exit_commands_only(loop) -> str:
    """A conversation woken ONLY to run slash commands: the commands already executed in
    boot, appending their events to the transcript. End the leg with NO model turn and NO
    authored reply (no finish event, result.md untouched) so the conversation returns to
    idle and the user keeps the speaking turn. The next PROSE message resumes normally and
    the model sees the command results replayed from the transcript.
    """
    loop.ctx.write_status("finished", question=None)
    return "finished"
