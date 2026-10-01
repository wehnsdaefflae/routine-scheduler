"""The assist RUNTIME — evaluating a rule's relevance triggers and delivering the line.

`rsched/assists.py` says what an assist IS and reads the library's declarations;
`assist_predicates.py` holds the checks. This file is the engine seam: four moments, one
delivery convention, and the guards that keep a layer which is live in every routine from
becoming rent on every turn.

The four moments, and why they deliver differently:

- **pre-action** — the action is HELD (`hold`), through the one interception seam a
  consequence reminder uses too (`engine/hold.py`); it costs a turn, which is why the rung is
  reserved for an irreversible cost to skipping.
- **observation** — the line rides the tail of the observation the run was getting anyway,
  the same no-turn carrier `remind.apply_ops` uses for `[REMINDERS: …]`.
- **boundary** — an appended ENGINE NOTE at the turn boundary, exactly the shape
  `switches.apply_rule_additions` already uses to put rule prose into a live thread. Append
  only: the composed prompt is a caching contract.
- **pre-finish** — a rung of the finish gate. This one COSTS a turn too, and has to: a line
  surfaced as the run ends is a line nobody can act on, so the finish is set aside and the
  model gets one more turn.

Two guards, both borrowed rather than invented. An assist fires **at most once per run**
(`loop.assists_fired`), the rule the hold ledger (`loop.holds`, engine/hold.py) and the claim
verifier's `_challenged` set already apply to their own interventions — a trigger that can
fire twice on one situation livelocks a stubborn model into a dead budget. And **at most one
finish deferral per run across all assists**, because the finish gate already has five rungs
that can each defer, and a sixth that can fire repeatedly would turn a run's ending into a
negotiation.

"Per run" is the GUARD SCOPE (engine/guardscope.py): every leg of a routine run, the current
reply of a conversation. A resumed leg rebuilds both guards — and the repos found clean — from
its transcript, so every event that carries an assist's line NAMES it (`assists`): the held
action's observation, an observation whose tail carried one, a boundary ENGINE NOTE, a finish
deferral. The observation-moment assists are asked before their observation is recorded for
exactly that reason (`EngineLoop._observe`).
"""

from __future__ import annotations

from pathlib import Path

from .. import assists as lib
from ..assists import Assist
from . import enginenote
from . import hold as hold_seam
from .assist_predicates import PREDICATES, Situation

#: This layer's name in the shared hold ledger (engine/hold.py). Its own budget: a reminder
#: holding an action must not spend the rule layer's only hold on the same action string.
SOURCE = "rule"


def configure(loop) -> None:
    """This layer's run state. Both guards are per guard scope (`rebuild` re-seeds them on a
    resumed leg): one fire per assist, and at most one finish held by one, ever.
    """
    loop.assists = load(loop)
    loop.assists_fired = set()
    loop.assist_finish_deferred = False
    loop.assist_user_replies = None     # the user-replies watermark, seeded at the 1st boundary
    loop.assist_undo_points = set()     # repos found clean this run: HEAD restores them
    loop.assist_undo_found = None       # …and the one found for the action in flight (`recorded`)


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed both guards and the undo points from the events inside the guard scope
    (engine/guardscope.py). Every event that carried an assist's line names it (`assists`), a
    deferral by this layer is the finish observation marked `assist`, and a repo found clean
    is named on the observation of the write its check let through (`undo_point`).

    The user-replies watermark is NOT carried: it is the leg's arrival edge by design — what
    the user said before a leg's first boundary is that leg's task (`at_boundary`).
    """
    loop.assists_fired = set()
    loop.assist_finish_deferred = False
    loop.assist_undo_points = set()
    for ev in events:
        if ev.get("type") not in ("observation", "user_injection"):
            continue
        payload = ev.get("payload") or {}
        loop.assists_fired.update(str(k) for k in payload.get("assists") or [])
        if payload.get("kind") == "finish" and payload.get("assist"):
            loop.assist_finish_deferred = True
        if payload.get("undo_point"):
            loop.assist_undo_points.add(Path(str(payload["undo_point"])))


def load(loop) -> list[Assist]:
    """The assists this run gets: those declared by the rules the routine HOLDS.

    Read ONCE at construction, like the reminder set and for the same reason — the composed
    prompt is append-only, and the rules layer's own doctrine is that a library revision lands
    at the next run, not mid-flight. What the user binds or unbinds WHILE the run is live is
    another matter: that reaches the run at once (`switches.apply_rule_additions` /
    `apply_rule_drop`), and the rule's assists go with it — `rules_bound` / `rules_unbound`.
    """
    ctx = loop.ctx
    try:
        return lib.for_rules(ctx.server.rules_home, list(ctx.routine.rules or []))
    except OSError:
        return []


def rules_bound(loop, slugs: list[str]) -> None:
    """Rules the user bound to the routine mid-run bring their assists: an assist is part of
    the rule, and the rule itself has just reached the run as a note.
    """
    loaded = {a.rule for a in loop.assists}
    try:
        new = lib.for_rules(loop.ctx.server.rules_home, [s for s in slugs if s not in loaded])
    except OSError:
        return
    loop.assists = [*loop.assists, *new]


def rules_unbound(loop, slugs: list[str]) -> None:
    """Rules the user unbound mid-run take their assists with them. The run is told the rule
    no longer binds it; its assists firing on — holding an action, deferring the finish — would
    be the rule still governing a routine that does not hold it.
    """
    gone = set(slugs)
    loop.assists = [a for a in loop.assists if a.rule not in gone]


def _fire(loop, assist: Assist, situation: Situation) -> bool:
    """Should this assist fire now? Marks it fired when yes — one per guard scope."""
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


def at_observation(loop, action: dict, obs: dict) -> list[Assist]:
    """The observation-moment assists that fire on this result — asked BEFORE the observation
    is recorded, so the record can name them (`recorded`); their line rides the observation's
    tail (`tail`). Costs no turn.
    """
    if not loop.assists:
        return []
    return _matching(loop, "observation", Situation(loop=loop, action=action, obs=obs))


def tail(fired: list[Assist]) -> str:
    """The tail appended to an observation — "" when nothing fired."""
    return ("\n" + "\n".join(_rendered(a) for a in fired)) if fired else ""


def recorded(loop, payload: dict, fired: list[Assist]) -> dict:
    """The observation as the transcript keeps it, with what this layer decided about the
    action named beside the result — the record a resumed leg rebuilds its guards from
    (`rebuild`): `assists` gains the observation-moment assists that fired on it (a hold
    already names its own), and `undo_point` names a repo the pre-action check found clean
    before letting this very action through. The payload itself when there is nothing to name.
    """
    marks: dict = {}
    if fired:
        marks["assists"] = [*(payload.get("assists") or []), *(a.key for a in fired)]
    if loop.assist_undo_found is not None:
        marks["undo_point"] = str(loop.assist_undo_found)
        loop.assist_undo_found = None
    return {**payload, **marks} if marks else payload


def at_boundary(loop) -> None:
    """Turn-boundary assists, appended as ENGINE NOTEs. Costs no turn.

    The `user_replies` watermark is advanced here whatever fired, so the arrival edge a
    predicate reads is the edge since the LAST boundary rather than since the run began. The
    leg's FIRST boundary only seeds it: whatever the user said before that — the message that
    opens a resumed leg, which is a conversation's every later reply, or one drained at boot —
    is the leg's task, not an intervention in it, exactly as a fresh run's opening prose is
    never counted at all. Counted from zero, it fired the correction line on every reply.
    """
    replies = int(getattr(loop.ctx, "user_replies", 0) or 0)
    if loop.assist_user_replies is None:
        loop.assist_user_replies = replies
    if loop.assists:
        for assist in _matching(loop, "boundary", Situation(loop=loop)):
            enginenote.append(loop, _rendered(assist), assists=[assist.key])
    loop.assist_user_replies = replies


def at_finish(loop, action: dict) -> tuple[str, list[str]] | None:
    """The pre-finish rung: the deferral message and the keys of the assists behind it, or
    None to let the finish stand.

    The caller (finishgate) owns the deferral SHAPE — this only decides whether one is owed
    and what it says; the keys ride the deferral's record (`rebuild`). Guarded like every
    other rung plus one of its own: a run may be held at its finish by an assist at most once,
    ever — once per reply, in a conversation.
    """
    if not loop.assists or loop.assist_finish_deferred:
        return None
    fired = _matching(loop, "pre-finish", Situation(loop=loop, action=action))
    if not fired:
        return None
    loop.assist_finish_deferred = True
    lines = "\n".join(_rendered(a) for a in fired)
    message = ("OBSERVATION (finish deferred): a general rule you practise applies to how this "
               f"run ends.\n{lines}\nAct on it and finish again — this is asked once per run "
               "(once per reply, in a conversation), so the next finish stands either way.")
    return message, [a.key for a in fired]
