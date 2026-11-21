"""The assist RUNTIME — evaluating a rule's relevance triggers and delivering the line.

`rsched/assists.py` says what an assist IS; `assist_predicates.py` holds the checks. Moments:

- **pre-action** — the action is HELD through the shared seam (`engine/hold.py`); costs a turn.
- **observation** — the line rides the tail of the observation (no turn).
- **boundary** — an appended ENGINE NOTE (append only: the prompt is a caching contract).
- **pre-finish** — a finish-gate rung; costs a turn, since a line surfaced at the end is
  otherwise unactionable.

Guards: an assist fires **at most once per guard scope** (`loop.assists_fired`) — twice on one
situation livelocks a stubborn model — and **at most one finish deferral across all assists**.
The scope is engine/guardscope.py's; a resumed leg rebuilds both guards from its transcript, so
every event carrying an assist's line NAMES it (`assists`), which is why observation-moment
assists are asked before their observation is recorded.
"""

from __future__ import annotations

from pathlib import Path

from .. import assists as lib
from ..assists import Assist
from ..hooks import dispatch, points
from . import assist_label, enginenote, notes
from . import hold as hold_seam
from .assist_predicates import PREDICATES, Situation

#: This layer's name in the shared hold ledger — its own budget, apart from the reminders'.
SOURCE = "rule"


def configure(loop) -> None:
    """This layer's run state; `rebuild` re-seeds the guards on a resumed leg."""
    loop.assists = load(loop)
    loop.assists_fired = set()
    loop.assist_finish_deferred = False
    loop.assist_user_replies = None     # the user-replies watermark, seeded at the 1st boundary
    loop.assist_undo_points = set()     # repos found clean this run: HEAD restores them
    loop.assist_undo_found = None       # …and the one found for the action in flight (`recorded`)
    loop.assist_owed = {}               # key → holds this run not yet labelled (assist_label)


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed both guards and the undo points from the events inside the guard scope: `assists`
    names fired lines, a finish observation marked `assist` is this layer's deferral, and
    `undo_point` names a repo found clean. The user-replies watermark is deliberately not
    carried — it is the leg's arrival edge (`at_boundary`).
    """
    loop.assists_fired = set()
    loop.assist_finish_deferred = False
    loop.assist_undo_points = set()
    assist_label.rebuild_owed(loop, events)   # the holds still owed a label (D239)
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
    """The assists declared by the rules the routine HOLDS, read ONCE: a library revision lands
    at the next run. A mid-run bind/unbind reaches the run via `rules_bound` / `rules_unbound`.
    """
    ctx = loop.ctx
    try:
        return lib.for_rules(ctx.server.rules_home, list(ctx.routine.rules or []))
    except OSError:
        return []


def rules_bound(loop, slugs: list[str]) -> None:
    """Rules bound mid-run bring their assists: an assist is part of the rule."""
    loaded = {a.rule for a in loop.assists}
    try:
        new = lib.for_rules(loop.ctx.server.rules_home, [s for s in slugs if s not in loaded])
    except OSError:
        return
    loop.assists = [*loop.assists, *new]


def rules_unbound(loop, slugs: list[str]) -> None:
    """Rules unbound mid-run take their assists with them, or the rule would still govern."""
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
    """The assists that fire at this moment, decided by the ONE hook-point evaluator
    (`hooks/dispatch.py` — the moment IS a hook point). The seam owns payload coupling and
    "a raising predicate did not fire"; this layer keeps its own once-per-scope guard and tally.
    """
    point = points.for_moment(moment)
    return dispatch.at(point, [a for a in loop.assists if a.moment == moment],
                       lambda a: _fire(loop, a, situation), payload=lambda a: a.payload)


def rendered_line(assist: Assist) -> str:
    """One line, one shape, every moment: what fired and where the full rule lives."""
    describes = PREDICATES[assist.predicate].describes
    return (f"[RULE {assist.rule} — {describes}] {assist.line} "
            f"(the full rule: read_rule name={assist.rule})")


def hold(loop, action: dict, rendered: str) -> dict | None:
    """This layer's answer for the shared pre-execution seam (`engine/hold.py`).

    A `pre-action` assist is always a HOLD — the action is already emitted, so stopping it is
    the only way the line can still matter. Overridable: re-emitting the action runs it;
    assistance informs, only capability checks gate.
    """
    if not loop.assists:
        return None
    if hold_seam.held_before(loop, SOURCE, rendered):
        return None
    fired = _matching(loop, "pre-action", Situation(loop=loop, action=action))
    if not fired:
        return None
    hold_seam.mark_held(loop, SOURCE, rendered)
    for a in fired:     # one hold, one label owed — the ledger `assist_label` settles against
        loop.assist_owed[a.key] = loop.assist_owed.get(a.key, 0) + 1
    return {"kind": "assist_hold", "action": rendered,
            "lines": [rendered_line(a) for a in fired],
            "assists": [a.key for a in fired]}


def at_observation(loop, action: dict, obs: dict) -> list[Assist]:
    """The observation-moment assists that fire on this result — asked BEFORE it is recorded,
    so the record can name them (`recorded`). Costs no turn.
    """
    if not loop.assists:
        return []
    return _matching(loop, "observation", Situation(loop=loop, action=action, obs=obs))


def tail(fired: list[Assist]) -> str:
    """The tail appended to an observation — "" when nothing fired."""
    return ("\n" + "\n".join(rendered_line(a) for a in fired)) if fired else ""


def recorded(loop, payload: dict, fired: list[Assist]) -> dict:
    """The observation as the transcript keeps it, plus what `rebuild` needs: `assists` gains
    the observation-moment assists that fired, `undo_point` a repo the pre-action check found
    clean. The payload itself when there is nothing to name.
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

    The `user_replies` watermark advances here whatever fired, so a predicate reads the edge
    since the LAST boundary. The leg's first boundary only seeds it: what the user said before
    that is the leg's task, not an intervention in it.
    """
    replies = int(getattr(loop.ctx, "user_replies", 0) or 0)
    if loop.assist_user_replies is None:
        loop.assist_user_replies = replies
    if loop.assists:
        for assist in _matching(loop, "boundary", Situation(loop=loop)):
            enginenote.append(loop, rendered_line(assist), assists=[assist.key])
    loop.assist_user_replies = replies





def at_child_exit(loop, child_row: dict) -> None:
    """Child-exit assists, appended as ENGINE NOTEs beside the hand-back. Costs no turn.

    Fired AFTER the hand-back is appended (`control.announce_finished_subruns`), so the note
    comments on something the model can see. Shaped like `at_boundary`; the first child that
    satisfies the predicate spends the once-per-scope assist.
    """
    if not loop.assists:
        return
    for assist in _matching(loop, "child-exit", Situation(loop=loop, child=child_row)):
        enginenote.append(loop, rendered_line(assist), assists=[assist.key])


def at_spawn(loop, start_row: dict) -> None:
    """Spawn assists, appended as ENGINE NOTEs beside the start row. Costs no turn.

    `child-exit`'s twin, and the one that can still change something (a child can be killed and
    re-briefed). Fired AFTER the start row is in the prompt (`control.announce_started_subruns`);
    once per scope like every rung.
    """
    if not loop.assists:
        return
    for assist in _matching(loop, "spawn", Situation(loop=loop, child=start_row)):
        enginenote.append(loop, rendered_line(assist), assists=[assist.key])


def at_run_end(loop, ending: dict) -> None:
    """Run-end assists, appended to `state/notes.md` for the NEXT run. Costs no turn, ever.

    Called by `loopend.finish_run` after `result.md` and before the autocommit, so the note is
    committed with the run that learned it (a rewind bypasses `finish_run` and cannot re-fire
    it). The carrier is `state/notes.md`, whose tail every digest renders; the stamp marks a
    hook's line apart from the run's own. Never raises — the run is already over.
    """
    if not loop.assists:
        return
    for assist in _matching(loop, "run-end", Situation(loop=loop, ending=ending)):
        notes.append_line(loop.ctx, rendered_line(assist), source="hook run-end")


def at_finish(loop, action: dict) -> tuple[str, list[str]] | None:
    """The pre-finish rung: the deferral message and the keys of the assists behind it, or
    None to let the finish stand.

    finishgate owns the deferral's shape; the keys ride its record (`rebuild`). At most one
    assist deferral per guard scope.
    """
    if not loop.assists or loop.assist_finish_deferred:
        return None
    fired = _matching(loop, "pre-finish", Situation(loop=loop, action=action))
    if not fired:
        return None
    loop.assist_finish_deferred = True
    lines = "\n".join(rendered_line(a) for a in fired)
    message = ("OBSERVATION (finish deferred): a general rule you practise applies to how this "
               f"run ends.\n{lines}\nAct on it and finish again — this is asked once per run "
               "(once per reply, in a conversation), so the next finish stands either way.")
    return message, [a.key for a in fired]
