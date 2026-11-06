"""The finish gate's RUNGS, declared — `pre-finalize`'s GUARD mechanism (D152 step 6).

The operator, 2026-10-26: *"The hook system also should allow to extend the implementation of
parts of the harness^2 features we already started."* `hooks/points.py`'s own docstring names
what stood in the way: *"a new point is an edit in five files, so the cheap thing to do is
hard-code a sixth call site, which is what every harness² feature did: `assist.py`,
`degrade.py`, `finishgate.py`, `autoask.py` and `remind.py` each reach into the loop at a moment
of their own"*. Step 4 moved `remind.py` onto the registry (`engine/remind_points.py`). This
module moves `finishgate.py`'s ladder — the other mechanism that fires at `pre-finalize`.

## What was wrong, concretely

`finishgate.check_finish` is nine guards in a row, and each one carries its own ACCESS POLICY
written out by hand: five copies of `ctx.depth == 0 and not loop._finish_reserved`, two more
that spell one half of it, and two that inherit it from whether `_owed` returned anything. The
policy is real and load-bearing — a child owes no accounting; deferring the RESERVED finish turn
force-finishes the run with an engine string and loses the summary the reserve exists to author
— but it was expressed only as a repeated conjunction. Three consequences:

1. **A tenth rung re-derives the policy.** Nothing said which guards skip the reserved turn or
   why, so each new one was written by copying its neighbour; a copy that dropped the
   `_finish_reserved` half would be invisible until a budget-exhausted run lost its summary.
2. **The asymmetries were unreadable.** `jobs_owed` is asked at ANY depth and on the reserved
   turn too (`_unanswered` calls `jobops.finish_owed` before either check) — deliberately, since
   one action settles it and a child can hold jobs. That is a real exception to the pattern and
   it was visible only by noticing which `if` it sits outside of.
3. **`pre-finalize` had a mechanism the registry could not name.** The point declared `fires:
   ("reminder", "rule")` while the ladder it is mostly MADE of fired there unlisted — the same
   defect step 4 found in the reminder layer, where routing the layer through the table exposed
   two entries that described a delivery nothing performs.

## What this changes, and what it deliberately does not

The policy is declared once per rung, and asked through the one hook-point evaluator:

    if finish_rungs.eligible(loop, ctx, "pending_user_input"):   # was: ctx.depth == 0 and …

So the gate inherits the seam's two invariants, identically with the rule and reminder layers:
a candidate whose payload `pre-finalize` cannot carry never fires, and a predicate that RAISES
means "did not fire" rather than a failed turn. The second one matters here more than anywhere:
this ladder runs on the finish path, where an exception costs a run its authored summary
(`finishgate.py`'s own module comment records the 2026-10-09 case, an `AttributeError` that
ended a run with no `result.md`).

What stays in `finishgate.py`: every rung's QUESTION, its wording, its deferral and the order
they are asked in. This module answers *may this rung be asked at all*, and nothing else — the
same boundary `hooks/dispatch.py` draws for every mechanism, so one layer can never spend
another's budget and the prose stays with the mechanism that owns it.

`once` is declared and NOT enforced here for the same reason: the rule rung's once-per-run
budget is `assist.at_finish`'s own state and the claim check's is `loop._challenged`, both of
which must survive a resume. Declaring it makes the ladder's shape readable without moving a
budget into a shared place where a second reader could spend it.

WHY A SEPARATE MODULE: `engine/finishgate.py` is 334 lines against the house's 350-line bar, so
the table does not fit there — and a declaration read by the registry belongs beside the one
function that reads it, as `engine/remind_points.py` is for the reminder layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..hooks import dispatch

#: The point this mechanism fires at. Named once: a string literal repeated per call site is
#: how `assists.MOMENTS` came to be a second vocabulary beside the registry.
POINT = "pre-finalize"

#: The name this mechanism carries in `points.MECHANISMS`.
MECHANISM = "guard"


@dataclass(frozen=True)
class Rung:
    """One guard on the finish ladder: what it asks, and WHO it may be asked of.

    Frozen like every record in `hooks/`: a reader that can edit the policy in place is how two
    callers come to disagree about what a point allows.
    """

    key: str
    about: str
    top_level_only: bool = True
    skip_reserved: bool = True
    once: bool = False
    #: Asked on a NARROW revise leg (engine/reviseleg.py)? False for every rung about the
    #: run's TASK — its jobs, its accounting, its rules, its `met` claims — which the
    #: proposing leg already answered; that leg's finish is where the gate's work was done.
    on_revise_leg: bool = True

    @property
    def payload(self) -> str:
        """What a rung DELIVERS at `pre-finalize`, in `points.PAYLOADS`' vocabulary.

        `remind` for every rung, and that is the registry's coupling rather than a default: the
        point's carrier is a `held_turn` — the finish is set aside and the model decides again —
        but what reaches the model is PROSE it reads, so the payload is a reminder delivered by
        holding, never a `hold` that prevents an action. `pre-action` is the one that prevents,
        and `points.allows` refuses the mix in both directions.
        """
        return "remind"


#: THE LADDER, in the order `finishgate.check_finish` asks it. `key` is the key the deferral
#: records in its observation (`engine/finishgate._defer`'s `**why`), which is what the console's
#: transcript reads to name the rung — so the table is keyed by the name that already exists
#: everywhere else rather than by a new one.
RUNGS: tuple[Rung, ...] = (
    Rung("pending_user_input",
         "a user message landed between this turn's inbox drain and the finish"),
    Rung("asked_back",
         "the operator replied to the approval this finish filed without deciding"),
    # The ONE exception to the pattern, and it is deliberate: `_unanswered` asks this before
    # either guard. A child run can hold jobs, and ONE action settles the rung (a checkpoint,
    # or `deferred` if the run sets the task aside), so it can never trap a run — which is the
    # reason the reserved turn is not exempt from it either.
    Rung("jobs_owed", "a task due this run has no checkpoint (gated processing)",
         top_level_only=False, skip_reserved=False, on_revise_leg=False),
    Rung("final_undeclared",
         "a conversation reply leaves a goal open without declaring itself final"),
    Rung("accounting", "the accounting field does not answer every line the finish owes",
         on_revise_leg=False),
    # The rule layer's own rung. `once` is ITS state (`assist.at_finish`), declared here so the
    # ladder's shape is readable; see the module docstring for why it is not enforced here.
    Rung("assist", "a general rule the routine practises whose moment is the ending itself",
         once=True, on_revise_leg=False),
    Rung("no_actions",
         "a top-level ok-finish as the very first action, with no observation behind any claim"),
    # Not exempt on the reserved turn by SKIPPING the question: the gate asks it there and
    # records an `error` event instead of deferring, because a rejection on that turn loses the
    # summary. So the rung is asked, and its DISPOSAL differs — which is why `skip_reserved`
    # stays false here and `finishgate` keeps that branch.
    Rung("unbacked_claims", "the summary claims a high-signal action the run never took",
         skip_reserved=False),
    Rung("claims_unsupported",
         "a `met` claim the run's own transcript does not support", once=True,
         on_revise_leg=False),
)

RUNG_BY_KEY: dict[str, Rung] = {r.key: r for r in RUNGS}

#: The keys, in ladder order — for a surface that lists the gate, and for the test that pins
#: this table against the gate it describes.
RUNG_KEYS = tuple(r.key for r in RUNGS)


def _policy_ok(loop, ctx, rung: Rung) -> bool:
    """The rung's declared access policy, asked of THIS finish.

    `getattr` on both reads: this predicate runs inside `dispatch.at`, where an exception means
    "did not fire" — so a loop or context missing either attribute would silently disable a
    guard. Defaulting explicitly keeps that decision here, where it is visible.
    """
    if rung.top_level_only and getattr(ctx, "depth", 0) != 0:
        return False
    if not rung.on_revise_leg and getattr(loop, "revise_leg", False):
        return False
    return not (rung.skip_reserved and getattr(loop, "_finish_reserved", False))


def eligible(loop, ctx, key: str) -> bool:
    """May the rung named `key` be ASKED of this finish? Decided by the one point evaluator.

    False for a key this build does not declare — lenient like every reader in `hooks/`, and
    safe in this direction: an undeclared rung is not asked, so a stale caller degrades to the
    finish standing rather than to a guard that fires on a policy nobody declared.
    """
    rung = RUNG_BY_KEY.get(str(key or ""))
    if rung is None:
        return False
    return bool(dispatch.at(POINT, [rung], lambda r: _policy_ok(loop, ctx, r),
                            payload=lambda r: r.payload))
