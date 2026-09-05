"""The assist RUNTIME — evaluating a rule's relevance triggers and delivering the line.

`rsched/assists.py` says what an assist IS and reads the library's declarations;
`assist_predicates.py` holds the checks. This file is the engine seam: three moments, one
delivery convention, and the guards that keep a layer which is live in every routine from
becoming rent on every turn.

The three moments, and why they deliver differently:

- **observation** — the line rides the tail of the observation the run was getting anyway,
  the same no-turn carrier `remind.apply_ops` uses for `[REMINDERS: …]`.
- **boundary** — an appended ENGINE NOTE at the turn boundary, exactly the shape
  `switches.apply_rule_additions` already uses to put rule prose into a live thread. Append
  only: the composed prompt is a caching contract.
- **pre-finish** — a rung of the finish gate. This one COSTS a turn, and has to: a line
  surfaced as the run ends is a line nobody can act on, so the finish is set aside and the
  model gets one more turn. It is the only moment here that spends anything.

Two guards, both borrowed rather than invented. An assist fires **at most once per run**
(`loop.assists_fired`), the rule `loop.reminder_held` and the stopping verifier's
`_challenged` set already apply to their own interventions — a trigger that can fire twice on
one situation livelocks a stubborn model into a dead budget. And **at most one finish
deferral per run across all assists**, because the finish gate already has five rungs that can
each defer, and a sixth that can fire repeatedly would turn a run's ending into a negotiation.
"""

from __future__ import annotations

from .. import assists as lib
from ..assists import Assist
from . import hold as hold_seam
from .assist_predicates import PREDICATES, Situation

#: This layer's name in the shared hold ledger (engine/hold.py). Its own budget: a reminder
#: holding an action must not spend the rule layer's only hold on the same action string.
SOURCE = "rule"


def configure(loop) -> None:
    """This layer's run state. Both guards are per-run: one fire per assist, and at most
    one finish held by one, ever.
    """
    loop.assists = load(loop)
    loop.assists_fired = set()
    loop.assist_finish_deferred = False
    loop.assist_user_replies = 0


def load(loop) -> list[Assist]:
    """The assists this run gets: those declared by the rules the routine HOLDS.

    Read ONCE at construction, like the reminder set and for the same reason — the composed
    prompt is append-only, and the rules layer's own doctrine is that a library revision lands
    at the next run, not mid-flight.
    """
    ctx = loop.ctx
    try:
        return lib.for_rules(ctx.server.rules_home, list(ctx.routine.rules or []))
    except OSError:
        return []


def _fire(loop, assist: Assist, situation: Situation) -> bool:
    """Should this assist fire now? Marks it fired when yes — one per run."""
    if assist.key in loop.assists_fired:
        return False
    predicate = PREDICATES.get(assist.predicate)
    if predicate is None:
        return False        # a rule naming a predicate this engine lost: inert, never fatal
    try:
        if not predicate.check(situation):
            return False
    except Exception:       # a predicate must never be able to fail a turn
        return False
    loop.assists_fired.add(assist.key)
    lib.record_fire(loop.ctx.routine.dir, assist)
    return True


def _matching(loop, moment: str, situation: Situation) -> list[Assist]:
    return [a for a in loop.assists if a.moment == moment and _fire(loop, a, situation)]


def _rendered(assist: Assist) -> str:
    """One line, one shape, every moment — so the run reads it at a glance and knows both
    what fired and where the rest of it lives.
    """
    describes = PREDICATES[assist.predicate].describes
    return (f"[RULE {assist.rule} — {describes}] {assist.line} "
            f"(the full rule: read_rule name={assist.rule})")


def hold(loop, action: dict, rendered: str) -> dict | None:
    """This layer's answer for the shared pre-execution seam (`engine/hold.py`).

    A `pre-action` assist is always a HOLD, and the coupling is not a policy choice: the
    action has already been emitted, so stopping it is the only way to put the rule's line in
    front of the model while it can still matter. The cost is a turn, which is why the design
    note reserves this rung for rules with a crisp pre-action predicate AND an irreversible
    cost to skipping — an undo point that does not exist yet, a reply that will not thread.

    Overridable by design: the same escape a reminder hold offers, re-emit the action and it
    runs. Assistance informs; even the strictest payload is a default, not a gate. The only
    hard gates in this system are the capability checks, which are the user's.
    """
    if not loop.assists:
        return None
    if hold_seam.held_before(loop, SOURCE, rendered):
        return None
    fired = _matching(loop, "pre-action", Situation(loop=loop, action=action))
    if not fired:
        return None
    hold_seam.mark_held(loop, SOURCE, rendered)
    return {"kind": "assist_hold", "action": rendered,
            "lines": [_rendered(a) for a in fired],
            "assists": [a.key for a in fired]}


def at_observation(loop, action: dict, obs: dict) -> str:
    """The tail appended to an observation — "" when nothing fired. Costs no turn."""
    if not loop.assists:
        return ""
    situation = Situation(loop=loop, action=action, obs=obs)
    fired = _matching(loop, "observation", situation)
    return ("\n" + "\n".join(_rendered(a) for a in fired)) if fired else ""


def at_boundary(loop) -> None:
    """Turn-boundary assists, appended as ENGINE NOTEs. Costs no turn.

    The `user_replies` watermark is advanced here whatever fired, so the arrival edge a
    predicate reads is the edge since the LAST boundary rather than since the run began.
    """
    ctx = loop.ctx
    if loop.assists:
        for assist in _matching(loop, "boundary", Situation(loop=loop)):
            note = _rendered(assist)
            ctx.transcript.event("user_injection", {"text": f"[engine] {note}",
                                                    "source": "engine"})
            loop.messages.append({"role": "user", "content": f"ENGINE NOTE: {note}"})
    loop.assist_user_replies = int(getattr(ctx, "user_replies", 0) or 0)


def at_finish(loop, action: dict) -> str | None:
    """The pre-finish rung: the deferral message, or None to let the finish stand.

    The caller (finishgate) owns the deferral SHAPE — this only decides whether one is owed
    and what it says. Guarded like every other rung plus one of its own: a run may be held at
    its finish by an assist at most once, ever.
    """
    if not loop.assists or loop.assist_finish_deferred:
        return None
    fired = _matching(loop, "pre-finish", Situation(loop=loop, action=action))
    if not fired:
        return None
    loop.assist_finish_deferred = True
    lines = "\n".join(_rendered(a) for a in fired)
    return ("OBSERVATION (finish deferred): a general rule you practise applies to how this "
            f"run ends.\n{lines}\nAct on it and finish again — this is asked once per run, "
            "so the next finish stands either way.")
