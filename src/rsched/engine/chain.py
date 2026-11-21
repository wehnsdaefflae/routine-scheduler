"""ACTION CHAINING — every action one reply wrote, run in order inside ONE turn.

- **Order.** The actions `replyactions.reply_actions` returns run in the order written.
- **Return.** Nothing is re-posted to the model between elements; every observation is read
  together at the start of the next turn.
- **`&&`.** The chain stops at the first element that FAILS; everything after it is named back
  unexecuted in the stopping element's observation (`replyactions.unexecuted_note`), so a
  resumed leg replays the same words.
- **One turn.** `ctx.turn` advances once per reply, but every ELEMENT gets its own
  `assistant_action` event, observation and message pair, so the transcript keeps action-level
  granularity (`endpoints/anthropic_messages.native_turns` reads one action per message).

What a chain must not quietly undo:

1. *The permission model.* Every later element goes through the SAME checks as the first
   (`completion.judge`); a refused element stops the chain instead of running.
2. *The write-grounding gate.* A read EARLIER IN THE SAME CHAIN has not been seen by the model,
   so it must not ground a later element's overwrite/delete; the chain's paths are held back
   until it ends (`_hold_grounding`).
3. *The false-claim shape.* A `finish` or `do_nothing` written after another action was composed
   before that result existed, so neither may be a later element.

OFF when `routine.yaml` says `chain_actions: false`: element 0 runs, the rest are reported
unexecuted.
"""

from __future__ import annotations

from . import hold, loopnudge, observations
from .actions import ALWAYS_KINDS
from .replyactions import unexecuted_row

#: How many actions of one reply the engine will run — a backstop, not the dial
#: (`chain_actions` is). The rest come back unexecuted and the run re-emits what it still wants.
MAX_CHAIN = 8

#: Kinds that may only be a reply's FIRST action: each makes a CLAIM about the world, which a
#: later element would compose before the earlier results existed.
CLAIM_KINDS = frozenset({"finish", "do_nothing"})

#: Why the engine ran one action, for the two replies it never chains at all.
OFF_REASON = ("action chaining is switched OFF for this routine, so the engine runs the FIRST "
              "action of a reply and no more")
RESERVED_REASON = ("this is the reserved finish turn, which executes one action and nothing "
                   "else")

#: What the RESERVED finish turn may still execute — the narrowed grammar's own vocabulary
#: (ALWAYS_KINDS, not a copy). A reserved turn never chains.
RESERVED_TURN_KINDS = frozenset(ALWAYS_KINDS)
#: The refusal of anything else there. An action refused with it ran nothing, not even its side
#: fields, which is how `remind_ledger.rebuild` tells it apart.
RESERVED_REFUSAL = ("the reserved finish turn executes nothing but `finish` (or "
                    + " / ".join(f"`{k}`" for k in ALWAYS_KINDS if k != "finish")
                    + ") — the budget is spent")


def configure(loop) -> None:
    """The chain's own per-run state, set once before the first turn (`loopsetup.configure`)."""
    loop.unexecuted = []       # this reply's actions that did NOT run (replyactions)
    loop.chain_queue = []      # …and the ones still to run in this turn, in order
    loop.chain_run = 1         # which element of the reply is running (1 = the first)
    loop.chain_seen = set()    # paths the chain itself read: not grounding until it ends
    loop.chain_opening = None  # seen_paths as the turn opened, while a chain holds it back


def enabled(loop) -> bool:
    """Is chaining on for this run? The routine's own switch, defaulting ON.

    A missing key reads as ON; nothing writes the field but a user turning it off.
    """
    return bool(getattr(loop.ctx.routine, "chain_actions", True))


def take_turn(loop, action: dict, streak: int) -> str | None:
    """Run this reply's actions and return the run's outcome, or None to take another turn.

    The whole of what one landed turn DOES: the finish branch, the reserved-turn refusal, the
    ordinary observe, then the rest of the chain. `loop.run` calls this once per turn.
    """
    queue = [a for a in loop.chain_queue if isinstance(a, dict)]
    chaining = bool(queue) and enabled(loop) and not loop._finish_reserved
    loop.unexecuted = [] if chaining else queue
    loop.chain_queue = queue if chaining else []
    loop.chain_run = 1
    if chaining:
        # what the run had read when this turn opened is all a chained write may inherit
        loop.chain_opening = set(loop.ctx.seen_paths)
    if action["kind"] == "finish":
        loop.unexecuted, loop.chain_queue = loop.unexecuted + loop.chain_queue, []
        return loop._finish(action)
    if loop._finish_reserved and action["kind"] not in RESERVED_TURN_KINDS:
        _refuse_on_reserved_turn(loop, action)
        return None
    loop._observe(action, streak)
    while loop.chain_queue:
        nxt = loop.chain_queue.pop(0)
        loop.chain_run += 1
        loop._land(nxt, chained=True)
        loop._observe(nxt, loopnudge.repeat_streak(loop, nxt))
    _release_grounding(loop)
    return None


def stop_fields(loop, obs: dict) -> dict:
    """The fields naming what did NOT run, for the observation being assembled right now.

    Asked by `loop._observe` once per executed action — where the `&&` decision is made and
    recorded. Returns `{}` while the chain goes on, `not_executed` rows + `chain_stopped` for a
    reply never chained (dial off, a finish, the reserved turn) or a chain that STOPS here.
    """
    if loop.unexecuted:
        return {"not_executed": [unexecuted_row(a) for a in loop.unexecuted],
                "chain_stopped": OFF_REASON if not enabled(loop) else RESERVED_REASON}
    if not loop.chain_queue:
        return {}
    reason = _stop_reason(loop, obs)
    if reason is None:
        _hold_grounding(loop)
        return {}
    loop.unexecuted, loop.chain_queue = loop.chain_queue, []
    return {"not_executed": [unexecuted_row(a) for a in loop.unexecuted],
            "chain_stopped": reason}


def _stop_reason(loop, obs: dict) -> str | None:
    """Why the chain stops after the action whose result is `obs`, or None to run the next one.

    `&&` first (a failed or held action), then the ends nothing may run past, then the next
    element's own fitness.
    """
    ctx = loop.ctx
    if hold.is_hold(obs):
        return ("the action above was HELD for your decision, so it has not run yet — "
                "nothing written after it could run either")
    if observations.is_failure(obs):
        return ("the action above FAILED, and a chain stops at its first failure (`&&`) — "
                "every action after it was written for a world where it had succeeded")
    if obs.get("cancelled"):
        return "the call above was cancelled from outside the run"
    if loop._aborted():
        return "the run is being aborted"
    if spent := ctx.budget_spent():
        return f"the run's budget is spent ({spent['message']})"
    if loop.chain_run >= MAX_CHAIN:
        return (f"a reply runs at most {MAX_CHAIN} actions — emit the rest once you have read "
                "these results")
    return _next_unfit(loop)


def _next_unfit(loop) -> str | None:
    """Why the NEXT queued element may not run, or None — and, when it may, the repaired
    version of it is put back in the queue so what runs is what the checks passed.

    A refused element is not retried — a schema retry would re-run the actions before it — and
    the reason lets the run re-emit it correctly.
    """
    from .completion import judge

    nxt = loop.chain_queue[0]
    if nxt.get("kind") in CLAIM_KINDS:
        return (f"a `{nxt['kind']}` only runs as a reply's FIRST action: written after another "
                "action, its account was composed before that action's result existed")
    repaired, problems = judge(loop, nxt)
    if problems:
        return (f"the next action did not pass the checks every action meets: "
                f"{'; '.join(str(p) for p in problems)[:400]}")
    loop.chain_queue[0] = repaired
    return None


def _hold_grounding(loop) -> None:
    """Keep what THIS chain has read out of the write-grounding set until the chain ends.

    The set as the TURN opened is put back before each further element; what the chain saw is
    accumulated and handed back by `_release_grounding` for the next turn.
    """
    ctx = loop.ctx
    if loop.chain_opening is None:   # chaining off: the turn's one action grounds as always
        return
    loop.chain_seen |= ctx.seen_paths - loop.chain_opening
    ctx.seen_paths = set(loop.chain_opening)


def _release_grounding(loop) -> None:
    """Give back everything the chain saw, once the whole reply has run."""
    if loop.chain_opening is not None:
        loop.ctx.seen_paths = set(loop.chain_opening) | loop.chain_seen | loop.ctx.seen_paths
        loop.chain_opening = None
        loop.chain_seen = set()


def _refuse_on_reserved_turn(loop, action: dict) -> None:
    """Record a non-finish emitted on the reserved finish turn — and do NOT run it.

    Keeps the "engine executes nothing else" promise on providers without constrained decoding;
    the next cycle's budget check force-finishes.
    """
    loop.ctx.transcript.event("observation", {
        "kind": action["kind"], "rejected": True, "reason": RESERVED_REFUSAL},
        turn=loop.ctx.turn)
