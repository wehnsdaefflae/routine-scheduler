"""The POINT REGISTRY — every moment in a run's life at which a hook can fire, in one table.

The operator, 2026-10-26: *"Also important events in runs in general like a pre-action hook,
post action hook, pre finalize hook, and many more (get inspired by Claude code's hooks). The
hook system also should allow to extend the implementation of parts of the harness^2 features
we already started"*. This module is the first half of that: the POINTS, named once.

## Why a registry, and what was wrong without one

Three of the points he names already fire. None of them was named anywhere. `pre-action` is
`engine/hold.py` asking two layers whether to stop an action; `post-action` is the observation
tail (`assist.at_observation`, `remind.at_observation`); `pre-finalize` is a rung of
`engine/finishgate.py` plus the `turn:finish` trigger in `engine/loop.py`. Each is a hard-coded
call site, which has three consequences this table removes:

1. **"Which points exist?" had no answer.** Not for the operator, not for a run, not for a test.
   The one surface that came close — `assists.MOMENTS` — lists the four an ASSIST may declare,
   which is a subset of the points that actually fire and says nothing about the rest.
2. **A new point is an edit in five files**, so the cheap thing to do is hard-code a sixth call
   site, which is what every harness² feature did: `assist.py`, `degrade.py`, `finishgate.py`,
   `autoask.py` and `remind.py` each reach into the loop at a moment of their own.
3. **A point that does NOT exist yet is invisible.** Nothing recorded that a run start, a spawn,
   a child's exit, a restart or a run end have no hook at all, so the gap could not be read off
   anything — only re-derived by reading the loop.

So the table carries the points that fire today AND the ones that do not (`fires: ()`), because
the registry is the answer to his question and a registry that lists only what is built cannot
say what is missing.

## What a point is NOT

A point is a MOMENT, not a trigger and not a delivery. `rsched/hooks/__init__.py` owns those:
a hook's `trigger` says what makes it fire (`send`/`match`/`moment`/`schedule`), its `delivery`
says when it arrives. A point is WHERE in the turn loop the engine stops to ask "does anything
fire here", and the two are orthogonal on purpose — `match` triggers fire at `pre-action` and
`post-action`, a `moment` trigger at the turn points, and a `send` hook at none of them (it is
delivered at a boundary, not evaluated at one).

Nothing here decides anything. `hooks/dispatch.py` is the evaluator; this is the vocabulary it
and every future caller agree on.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What a hook may DO when it fires at a point. The axis is `remind -> hold`, as
#: `assists.PAYLOADS` already spells it, plus `note` for the points whose carrier is an engine
#: note rather than an observation tail.
#:
#: `action` — PERFORM one of the engine's own actions at the point, with no model turn — is the
#: Claude-Code half of the operator's ask, and his answer (2026-10-26) was *"Any action. This
#: includes utils and therefore scripts."* He named the ACTION VOCABULARY, not a sandbox or a
#: command form, so the payload is built on the seam that already runs an authored action
#: through the model's own path (`hooks/perform.py`, lifted from `control.run_user_command`).
#: It is allowed at the points in `ACTION_POINTS` only — see there for the half still unruled.
PAYLOADS = ("remind", "note", "hold", "action")

#: The CARRIER of a payload at a point — how what fires reaches the model.
#:
#: - `observation_tail` — appended to the observation the run was getting anyway. Free.
#: - `engine_note`      — an appended engine note at a turn boundary. Free.
#: - `held_turn`        — the action does not run and the model decides again. Costs a turn,
#:                        and is the ONLY carrier that can change what executes.
#: - `prompt_section`   — it rides the next prompt (a report, an injection). Free, next turn.
CARRIERS = ("observation_tail", "engine_note", "held_turn", "prompt_section")

#: The mechanisms that can fire at a point today. A point naming none is a point nothing
#: reaches yet — the gap the registry exists to make readable.
MECHANISMS = ("reminder", "rule", "report", "injection", "runtree")


@dataclass(frozen=True)
class Point:
    """One hook point: when it happens, what it can reach, and who fires there today.

    Frozen for the reason every record in this package is: a reader that can edit the table in
    place is how two callers come to disagree about what the point allows.
    """

    name: str
    about: str
    payloads: tuple[str, ...]
    carrier: str
    fires: tuple[str, ...] = ()
    site: str = ""

    @property
    def holds(self) -> bool:
        """Can a hook at this point change what runs? Only a held turn can."""
        return self.carrier == "held_turn"

    @property
    def live(self) -> bool:
        """Does anything fire here today?"""
        return bool(self.fires)


def _p(name: str, about: str, payloads: tuple[str, ...], carrier: str,
       fires: tuple[str, ...] = (), site: str = "") -> tuple[str, Point]:
    return name, Point(name=name, about=about, payloads=payloads, carrier=carrier,
                       fires=fires, site=site)


#: THE TABLE. Ordered as a turn runs: the run's own edges around the turn's edges.
#:
#: `site` names the engine seam that fires the point — the one place to look, and the thing
#: that would otherwise have to be re-derived by reading the loop. A point with no `fires` has
#: no site either; naming the module that WOULD fire it is a guess, and this table is read as
#: fact.
POINTS: dict[str, Point] = dict([
    _p("run-start", "the run's first turn is about to be composed", ("note", "action"),
       "engine_note", ("reminder",), "engine/remind.py (trigger kind `turn:first`)"),
    _p("pre-action", "an action has been chosen and has NOT run", ("hold",), "held_turn",
       ("reminder", "rule"), "engine/hold.py"),
    _p("post-action", "the action ran and its observation is about to be recorded",
       ("remind", "action"), "observation_tail", ("reminder", "rule"),
       "engine/loop.py::_observe (assist.at_observation, remind.at_observation)"),
    _p("turn-boundary", "the turn ended and the next prompt is being assembled",
       ("note", "remind", "action"), "engine_note",
       ("reminder", "rule", "injection", "runtree"),
       "engine/loop.py (assist.at_boundary, control.drain_injections)"),
    _p("pre-finalize", "a finish was emitted and has not been accepted", ("remind",),
       "held_turn", ("reminder", "rule"),
       "engine/finishgate.py (assist.at_finish) + engine/loop.py::_finish (remind.at_finish)"),
    _p("question", "the run asked a person something", ("remind", "action"),
       "observation_tail", ("reminder",), "engine/remind.py (trigger kind `turn:question`)"),
    _p("answer", "a person's reply reached the run", ("remind", "action"),
       "observation_tail", ("reminder",), "engine/remind.py (trigger kind `turn:answer`)"),
    _p("error", "an observation came back reporting failure", ("remind", "action"),
       "observation_tail", ("reminder",), "engine/remind.py (trigger kind `turn:error`)"),
    # Points nothing fires at yet. Each is a moment the engine already passes through, named
    # here so the gap is readable; the campaign's later steps fire them.
    _p("spawn", "a child run was started", ("note", "action"), "engine_note"),
    _p("child-exit", "a child run finished and its hand-back landed", ("note", "action"),
       "engine_note"),
    _p("run-end", "the run is over and its result is written", ("note", "action"),
       "prompt_section"),
    _p("restart", "the daemon is restarting under a live run", ("note", "action"),
       "prompt_section"),
])

#: The points that exist as of this step — the ordered names, for a surface that lists them.
POINT_NAMES = tuple(POINTS)

#: The points a hook can fire at TODAY. Read by `dispatch.at`, and the honest half of the
#: answer to "are hooks fully implemented".
LIVE_POINTS = tuple(n for n, p in POINTS.items() if p.live)

#: The points that can change what runs — TWO, and a correction to the design's rule 2.
#:
#: `state/hooks-design.md` (2026-10-18) says `hold_before_action` is the only delivery that can
#: change what runs, because "every other moment rides an observation or a prompt section".
#: That was true when it was written. It is not true of this engine: the pre-finish rung
#: (`engine/finishgate.py` asking `assist.at_finish`) SETS THE FINISH ASIDE, so the run takes a
#: turn it would not have taken. It costs a turn and it alters control flow, which is what the
#: rule is about — building this table is what made the second one visible.
#:
#: They differ in kind and both are deliberate: `pre-action` PREVENTS an action, `pre-finalize`
#: DEFERS a finish. A THIRD entry appearing here is a decision, not a refactor.
HOLDING_POINTS = tuple(n for n, p in POINTS.items() if p.holds)

#: The points at which a hook may PERFORM an action (`hooks/perform.py`). Derived, not a second
#: list: a point carries `action` iff it cannot change control flow, i.e. iff it is not in
#: `HOLDING_POINTS`.
#:
#: Why that line, and not "everywhere, since he said any action": TWO rulings, both his.
#:
#: 1. What may RUN — *"Any action. This includes utils and therefore scripts"* (2026-10-26).
#:    `validate_action` bounds it to the firing routine's own capabilities for free.
#: 2. What may VETO — *"No veto ever — a hook performs and reports; only a hold (prose to the
#:    model) can stop an action"* (2026-10-26, answering whether a performed action's FAILURE
#:    may refuse the action the run was about to take).
#:
#: So this is not a conservative first cut awaiting a widening: a performed action NEVER changes
#: control flow, which is exactly why the two points that do change it (`pre-action`,
#: `pre-finalize`) carry `hold` and not `action`. `HOLDING_POINTS` stays pinned by count, and
#: that pin now records his decision rather than an open question. Stopping an action remains the
#: one thing a hook does by TELLING the model, never by acting behind it.
ACTION_POINTS = tuple(n for n, p in POINTS.items() if "action" in p.payloads)


def point(name: str) -> Point | None:
    """One point by name, or None. Lenient like `hooks.from_record`: a caller naming a point
    this build does not have gets nothing to fire rather than an exception, because a hook
    store shared by migrating mechanisms must survive a row written by a newer build.
    """
    return POINTS.get(str(name or ""))


def allows(name: str, payload: str) -> bool:
    """May a hook with this payload fire at this point? False for an unknown point.

    The coupling is the registry's substance, not bookkeeping: at `pre-action` the action has
    already been emitted, so "remind and let it run" is not expressible there — the only way to
    reach the model is to stop the action. `assists.MOMENT_PAYLOADS` says the same thing for
    the four moments a rule may declare, and `tests/test_hooks_points.py` pins the two tables
    against each other so they cannot drift into disagreeing.
    """
    p = point(name)
    return bool(p and payload in p.payloads)


#: assist moment -> point name. The rule layer's four moments ARE four of these points under
#: older names; this is the one place that correspondence is written down, so the registry
#: subsumes `assists.MOMENTS` instead of being a second vocabulary beside it.
ASSIST_MOMENTS = {"pre-action": "pre-action", "observation": "post-action",
                  "boundary": "turn-boundary", "pre-finish": "pre-finalize"}

#: reminder trigger kind -> point name. `action` is tested before dispatch (so it holds) and
#: `result`/`prose` after it; the `turn:*` kinds are the run's own moments.
TRIGGER_POINTS = {"action": "pre-action", "result": "post-action", "prose": "turn-boundary",
                  "turn:first": "run-start", "turn:finish": "pre-finalize",
                  "turn:question": "question", "turn:answer": "answer",
                  "turn:error": "error"}


def for_moment(moment: str) -> str:
    """The point an assist moment fires at — "" for an unknown moment."""
    return ASSIST_MOMENTS.get(str(moment or ""), "")


def for_trigger(kind: str) -> str:
    """The point a reminder trigger kind is tested at — "" for an unknown kind."""
    return TRIGGER_POINTS.get(str(kind or ""), "")
