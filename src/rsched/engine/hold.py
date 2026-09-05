"""The pre-execution HOLD seam — one interruption per action, whatever asked for it.

Two layers can now stop an action before it runs: a CONSEQUENCE REMINDER (`engine/remind.py`,
a pattern this routine learned from its own surprise) and a RULE ASSIST (`engine/assist.py`, a
moment the library declared on a curated rule). They are the two authorship faces of one
relevance-trigger layer, and this is the one place either of them reaches the turn loop.

Extracted when the second caller appeared, not before — but the extraction is not tidiness.
Two things go wrong if each layer owns its own interception:

**The ledger key.** A hold is remembered so that re-emitting the same action IS the
confirmation to proceed. Keyed on the bare action string, the two sources would cannibalise
each other: a reminder holding `util:fs-ops mv a b` would silently spend the rule layer's one
allowed hold on the same string, and the rule's caution would never be seen. The key carries
the SOURCE, so each layer gets its own one-per-action-string budget.

**One interruption per action.** However many sources match, the model is stopped once. The
anti-livelock reasoning — a model and a gate that both refuse to yield burn the budget between
them — applies to the PAIR, not to each layer separately, so precedence resolves it rather
than queueing a second hold behind the first.

Precedence is specific-before-general: a reminder is evidence THIS routine gathered about THIS
action, a rule is a standing principle that applies to everyone. When both fire, the run hears
the one it learned itself.
"""

from __future__ import annotations

from .actionschema import canon

#: Every observation kind that means "the action did not run". The literal used to be tested
#: in three modules, one of them NEGATIVELY (the resume rebuild of `executed_actions`), which
#: is the kind of check a second hold kind silently walks past.
HOLD_KINDS = frozenset({"reminder_hold", "assist_hold"})


def is_hold(obs: dict) -> bool:
    """Did this observation report a HELD action — one the engine did not execute?

    The predicate behind two consequences that must stay in step: a held action grounds no
    finish (`executed_actions`, live and rebuilt on resume) and spends no allow-once grant
    (spent by USE, not by attempt).
    """
    return bool(obs) and obs.get("kind") in HOLD_KINDS


def held_before(loop, source: str, rendered: str) -> bool:
    """Has THIS source already held THIS action string in this run?"""
    return (source, rendered) in loop.holds


def mark_held(loop, source: str, rendered: str) -> None:
    loop.holds.add((source, rendered))


def before_dispatch(loop, action: dict) -> dict | None:
    """Ask each source, in precedence order, for a reason to hold this action.

    Returns the observation the model reads INSTEAD of the action's result, or None to let it
    execute. The canonical string is computed once here and handed to every source, so the two
    layers can never disagree about what the action was.
    """
    from . import assist, remind

    rendered = canon(action)
    for source in (remind, assist):
        obs = source.hold(loop, action, rendered)
        if obs is not None:
            return obs
    return None
