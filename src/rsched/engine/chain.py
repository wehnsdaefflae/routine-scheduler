"""ACTION CHAINING — every action one reply wrote, run in order inside ONE turn.

The operator asked for this in his own words (2026-10-23): *"I want action chaining to work
like in the terminal. Chained actions provide their return values together in the next run"*,
and then *"on for every routine but can be toggled off in the routine settings"* with the
correction *"stop at the first failed action, like &&"*. So:

- **Order.** `replyactions.reply_actions` already returns every action a reply carried, in the
  order it wrote them (its docstring says why the order is what the model meant). They run in
  that order — this module is the loop that runs them.
- **Return.** A chained action never sees an earlier one's result INSIDE the turn: nothing is
  re-posted to the model between elements. Every observation lands in the message list and the
  model reads them all together at the start of its next turn, the way a terminal prints each
  command's output before the next prompt.
- **`&&`.** The chain stops at the first element that FAILS, and everything written after it is
  reported back unexecuted by name, in the stopping element's own observation — so the record a
  resumed leg replays is the same words (`replyactions.unexecuted_note`).
- **One turn.** `ctx.turn` advances once, for the whole reply: a chain is one model call, which
  is what it saves. Every ELEMENT still gets its own `assistant_action` event, its own
  observation event and its own pair of messages, so the transcript keeps action-level
  granularity and the Anthropic adapter still renders each as the tool call it was
  (`endpoints/anthropic_messages.native_turns` reads one JSON object per assistant message).

**What a chain must not quietly undo.** Three things earlier findings put in place:

1. *The permission model.* `completion.action_candidate` validates only the FIRST action. Every
   later element is put through the SAME four checks plus the parameter repair, through
   `completion.judge` — schema, per-kind/capability/grant, a denied authoring recreate, a denied
   access request — and a refused element stops the chain instead of running. Without that a
   chain would be a hole in the capability system.
2. *The write-grounding gate.* `write_file` refuses to overwrite a file outside the routine's own
   directory that this run has never READ, and `fsops` guards delete/move the same way
   (`ctx.seen_paths`). Inside a chain the model has NOT seen what an earlier element read — the
   results arrive next turn — so a read EARLIER IN THE SAME CHAIN must not ground a later
   element's overwrite. The paths a chain sees are therefore held back until the chain ends
   (`_hold_grounding`), and the model grounds its write with the read it has actually read.
3. *The false-claim shape.* A `finish` or a `do_nothing` written after another action was authored
   before that action's result existed — exactly the R2443-R2445 failure (17 false claims in two
   runs). Neither may be a chain's later element: it is reported unexecuted with that reason, and
   the run gets to write its summary against observations that exist.

OFF for a routine whose `routine.yaml` says `chain_actions: false` — then element 0 runs and the
rest are reported unexecuted, exactly as before this module existed.
"""

from __future__ import annotations

from . import hold, loopnudge, observations
from .actions import ALWAYS_KINDS
from .replyactions import unexecuted_row

#: How many actions of one reply the engine will run. A backstop, not the dial: the operator's
#: switch is `chain_actions`, and a reply writing more than this many actions is not chaining,
#: it is planning out loud — the rest come back unexecuted and the run re-emits what it still
#: wants. 8 is the length past which a reply's later actions stop being about what it just read.
MAX_CHAIN = 8

#: Kinds that may only ever be a reply's FIRST action: each makes a CLAIM about the world
#: (`finish`'s summary and accounting, `do_nothing`'s "nothing was due") and one written after
#: another action was composed before that action's result existed.
CLAIM_KINDS = frozenset({"finish", "do_nothing"})

#: Why the engine ran one action, for the two replies it never chains at all.
OFF_REASON = ("action chaining is switched OFF for this routine, so the engine runs the FIRST "
              "action of a reply and no more")
RESERVED_REASON = ("this is the reserved finish turn, which executes one action and nothing "
                   "else")

#: What the RESERVED finish turn may still execute — the narrowed grammar's own vocabulary
#: (`kindsurface.schema_for_kinds({"finish"})` carries ALWAYS_KINDS with it), so it is that
#: tuple rather than a copy of it. Spending the reserve on a `report` instead of a `finish` is
#: the model's call and costs it the authored summary; anything else is an action on a turn the
#: model was told executes nothing. A reserved turn never chains: the promise is that the
#: engine executes NOTHING else, and one action is the whole of what it still allows.
RESERVED_TURN_KINDS = frozenset(ALWAYS_KINDS)
#: The refusal of anything else there. An action refused with it ran nothing — not even the
#: side fields every other turn applies — which is how `remind_ledger.rebuild` tells it apart.
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

    A missing key reads as ON, which is why the config field defaults to True and nothing
    writes it but a user turning it off (R2782: a scaffold that wrote an explicit value left 28
    routines on a superseded default for weeks).
    """
    return bool(getattr(loop.ctx.routine, "chain_actions", True))


def take_turn(loop, action: dict, streak: int) -> str | None:
    """Run this reply's actions and return the run's outcome, or None to take another turn.

    The whole of what one landed turn DOES, for one action or eight: the finish branch, the
    reserved-turn refusal, the ordinary observe, and then the rest of the chain. `loop.run`
    calls exactly this, once per turn.
    """
    queue = [a for a in loop.chain_queue if isinstance(a, dict)]
    chaining = bool(queue) and enabled(loop) and not loop._finish_reserved
    loop.unexecuted = [] if chaining else queue
    loop.chain_queue = queue if chaining else []
    loop.chain_run = 1
    if chaining:
        # BEFORE the first element runs: what the run had already read when this turn opened
        # is the only grounding a chained write may inherit (module docstring, point 2).
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

    Asked by `loop._observe` once per executed action, with its result in hand — the
    one moment the chain's `&&` decision can be made and recorded where the run will read it.
    Returns `{}` while the chain goes on, `{"not_executed": rows}` for a reply the engine never
    chained (the dial off, a finish, the reserved turn) and both keys when the chain STOPS here:
    the remaining actions move to `loop.unexecuted`, so the loop in `take_turn` ends and the
    rows are the same ones a resumed leg replays.
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

    `&&` first — a failed or held action is the case the operator named and the one the
    false-claim findings are about: everything written after it was written for a world where it
    succeeded. Then the ends nothing may run past, then the next element's own fitness.
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

    Every element meets the checks the first action meets (`completion.judge`). A refused one is
    not run and not retried: a schema retry re-posts the whole reply, which would run the
    actions before it a second time. Saying why is what lets the run re-emit it correctly.
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

    Module docstring, point 2: a read earlier in the same chain is not something the model has
    seen — its result arrives next turn — so it may not ground a later element's overwrite or
    delete. The set as the TURN opened is snapshotted once and put back before each further
    element; what the chain itself saw is accumulated and handed back by `_release_grounding`,
    so the next turn (the one that has read those results) is grounded exactly as always.
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

    "This is your LAST turn — the engine executes nothing else" is a promise, and it was only a
    promise: the reserved turn's grammar is narrowed to `finish`, but a provider without
    constrained decoding can emit any kind and the executor ran it. The budget check that opens
    the next cycle force-finishes — the same ending as before, minus the action.
    """
    loop.ctx.transcript.event("observation", {
        "kind": action["kind"], "rejected": True, "reason": RESERVED_REFUSAL},
        turn=loop.ctx.turn)
