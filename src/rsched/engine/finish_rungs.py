"""The finish gate's RUNGS, declared — `pre-finalize`'s GUARD mechanism (D152 step 6).

`finishgate.check_finish` is a ladder of guards, each with an ACCESS POLICY — a child owes no
accounting; deferring the RESERVED finish turn would force-finish the run and lose the summary the
reserve exists to author. That policy is declared here once per rung, so a new rung cannot drop
half of it by copying a neighbour, the exceptions (`jobs_owed`) are visible, and the registry can
name the mechanism that fires at `pre-finalize`:

    if finish_rungs.eligible(loop, ctx, "pending_user_input"):

Asked through the one hook-point evaluator, so the gate inherits its invariants: a candidate whose
payload the point cannot carry never fires, and a predicate that RAISES means "did not fire"
rather than a failed turn — which matters most on the finish path.

What stays in `finishgate.py`: every rung's QUESTION, wording, deferral and order. This module
answers only *may this rung be asked at all*. `once` is declared, NOT enforced here: each rung's
once-budget is its own resume-surviving state (`assist.at_finish`, `loop._challenged`).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..hooks import dispatch

#: The point this mechanism fires at, named once (no per-call-site literal).
POINT = "pre-finalize"

#: The name this mechanism carries in `points.MECHANISMS`.
MECHANISM = "guard"


@dataclass(frozen=True)
class Rung:
    """One guard on the finish ladder: what it asks, and WHO it may be asked of.

    Frozen like every record in `hooks/`, so no reader can edit the policy in place.
    """

    key: str
    about: str
    top_level_only: bool = True
    skip_reserved: bool = True
    once: bool = False
    #: Asked on a NARROW revise leg (engine/reviseleg.py)? False for every rung about the
    #: run's TASK, which the proposing leg already answered.
    on_revise_leg: bool = True

    @property
    def payload(self) -> str:
        """What a rung DELIVERS at `pre-finalize`, in `points.PAYLOADS`' vocabulary.

        Always `remind`: the finish is set aside and the model reads PROSE, a reminder delivered
        by holding — never a `hold` that prevents an action (only `pre-action` prevents).
        """
        return "remind"


#: THE LADDER, in the order `finishgate.check_finish` asks it. `key` is the key the deferral
#: records in its observation (`finishgate._defer`'s `**why`), which the console reads.
RUNGS: tuple[Rung, ...] = (
    Rung("pending_user_input",
         "a user message landed between this turn's inbox drain and the finish"),
    Rung("asked_back",
         "the operator replied to the approval this finish filed without deciding"),
    # Deliberately asked at any depth and on the reserved turn: a child can hold jobs, and one
    # action settles the rung, so it can never trap a run.
    Rung("jobs_owed", "a task due this run has no checkpoint (gated processing)",
         top_level_only=False, skip_reserved=False, on_revise_leg=False),
    Rung("final_undeclared",
         "a conversation reply leaves a goal open without declaring itself final"),
    Rung("accounting", "the accounting field does not answer every line the finish owes",
         on_revise_leg=False),
    # the rule layer's rung; its `once` is `assist.at_finish`'s state
    Rung("assist", "a general rule the routine practises whose moment is the ending itself",
         once=True, on_revise_leg=False),
    Rung("no_actions",
         "a top-level ok-finish as the very first action, with no observation behind any claim"),
    # Asked on the reserved turn too, but there `finishgate` records an `error` event instead
    # of deferring (a rejection would lose the summary).
    Rung("unbacked_claims", "the summary claims a high-signal action the run never took",
         skip_reserved=False),
    Rung("claims_unsupported",
         "a `met` claim the run's own transcript does not support", once=True,
         on_revise_leg=False),
)

RUNG_BY_KEY: dict[str, Rung] = {r.key: r for r in RUNGS}

#: The keys, in ladder order.
RUNG_KEYS = tuple(r.key for r in RUNGS)


def _policy_ok(loop, ctx, rung: Rung) -> bool:
    """The rung's declared access policy, asked of THIS finish.

    `getattr` with explicit defaults: inside `dispatch.at` an exception means "did not fire",
    so a missing attribute would silently disable a guard.
    """
    if rung.top_level_only and getattr(ctx, "depth", 0) != 0:
        return False
    if not rung.on_revise_leg and getattr(loop, "revise_leg", False):
        return False
    return not (rung.skip_reserved and getattr(loop, "_finish_reserved", False))


def eligible(loop, ctx, key: str) -> bool:
    """May the rung named `key` be ASKED of this finish? Decided by the one point evaluator.

    False for an undeclared key, so a stale caller degrades to the finish standing.
    """
    rung = RUNG_BY_KEY.get(str(key or ""))
    if rung is None:
        return False
    return bool(dispatch.at(POINT, [rung], lambda r: _policy_ok(loop, ctx, r),
                            payload=lambda r: r.payload))
