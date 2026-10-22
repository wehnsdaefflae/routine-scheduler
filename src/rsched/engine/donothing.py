"""The `do_nothing` action (D186): a run states that nothing was due, and WHY.

A run fired on a schedule often finds nothing to do. Ending there is right, and `finish`
has always been able to say so. What a run could NOT do was record the finding as a turn —
so one that wanted to show its reasoning before ending invented an action to show it with,
and one that did not left a transcript in which "the sources were empty" and "the sources
were never read" look identical.

This kind is that statement. It has no fields: the reasoning is `say`, which rides every
kind, is published by the console, and is rendered by the transcript for every kind — so
this adds no schema field and no transcript event type. Its one grammatical rule lives in
`actions.validate_action`: `say` must be non-empty here, because here it is the action.

IT GROUNDS NOTHING. `loop.executed_actions` counts the actions that could ground a
top-level `finish(ok)` against the fabrication guard (engine/finishgate.py), and this kind
is deliberately not among them — `grounds_a_finish` is the predicate, asked by the live loop
and by the resume rebuild in `boot`, exactly as `hold.is_hold` is. A run whose only action is
`do_nothing` has established nothing: counting it would let any run pass the guard by writing
one word, which is the hallucinated-completion shape the guard exists to refuse. This costs an
honestly idle run nothing. The guard only ever refused an `ok` finish that NOTHING backed, and
such a run's options are unchanged: finish, with the same reasoning in the summary.
"""

from __future__ import annotations

#: Observation kinds that executed nothing a finish could rest on. A hold is the other
#: family (`engine/hold.py` owns it, and a hold never reached the dispatcher at all); this
#: one DID dispatch and still establishes nothing about the world.
NON_GROUNDING_KINDS = frozenset({"do_nothing"})


def grounds_a_finish(obs: dict) -> bool:
    """May this observation ground a top-level `finish(ok)`?

    False for an action the engine did not run (a hold) and for one whose whole content is a
    statement about there being nothing to run (`do_nothing`). The live loop and the resume
    rebuild both ask this one predicate, so the counter cannot mean one thing in a fresh run
    and another in a resumed leg — the drift `hold.is_hold` was extracted to prevent.
    """
    from .hold import is_hold

    return bool(obs) and not is_hold(obs) and obs.get("kind") not in NON_GROUNDING_KINDS


def handle(action: dict) -> dict:
    """Record the statement. The reasoning is echoed back so the run reads what it just
    recorded rather than a bare acknowledgement.
    """
    return {"kind": "do_nothing", "reasoning": str(action.get("say") or "").strip()}
