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

"Already held" means within the GUARD SCOPE — the whole run for a routine, the current reply
for a conversation (engine/guardscope.py). A resumed leg rebuilds the ledger from the holds
its transcript recorded, so the confirmation a run gave before a restart still stands after
it; the hold's observation IS the record, its kind naming the source and `action` the string.
"""

from __future__ import annotations

from .actionschema import canon

#: Every observation kind that means "the action did not run", with the SOURCE whose ledger
#: entry it records (each layer's own `SOURCE`). The kinds used to be a literal tested in three
#: modules, one of them NEGATIVELY (the resume rebuild of `executed_actions`), which is the kind
#: of check a second hold kind silently walks past — so `is_hold` and the ledger's rebuild read
#: this one table, and a third hold kind is a row here.
HOLD_SOURCES = {"reminder_hold": "reminder", "assist_hold": "rule"}
HOLD_KINDS = frozenset(HOLD_SOURCES)


def configure(loop) -> None:
    """This seam's run state: which (source, action) pairs have already been held."""
    loop.holds = set()


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed the ledger from the holds recorded inside the guard scope (engine/guardscope.py)
    — a resumed leg's counterpart of `configure`. A hold needs no extra record: its observation
    is the hold, its kind names the source and its `action` the canonical string.
    """
    loop.holds = set()
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "observation" and is_hold(payload):
            loop.holds.add((HOLD_SOURCES[payload["kind"]], str(payload.get("action") or "")))


def is_hold(obs: dict) -> bool:
    """Did this observation report a HELD action — one the engine did not execute?

    The predicate behind two consequences that must stay in step: a held action grounds no
    finish (`executed_actions`, live and rebuilt on resume) and spends no allow-once grant
    (spent by USE, not by attempt).
    """
    return bool(obs) and obs.get("kind") in HOLD_KINDS


def held_before(loop, source: str, rendered: str) -> bool:
    """Has THIS source already held THIS action string in this guard scope — this run, or
    this reply of a conversation?
    """
    return (source, rendered) in loop.holds


def mark_held(loop, source: str, rendered: str) -> None:
    loop.holds.add((source, rendered))


def before_dispatch(loop, action: dict) -> dict | None:
    """Ask each source, in precedence order, for a reason to hold this action.

    Returns the observation the model reads INSTEAD of the action's result, or None to let it
    execute. The canonical string is computed once here and handed to every source, so the two
    layers can never disagree about what the action was.

    A write carrying a script (`then_script`, engine/thenscript.py) is asked about TWICE: as
    itself, then as the script it runs, rendered as that script would be alone — a reminder
    on `script:deploy …` must hold the fused call as surely as the bare one. A held script
    holds the whole action (nothing runs), and the observation names what it rode on.
    """
    from . import assist, remind, thenscript

    script = thenscript.riding(action)
    for part in (action, script) if script is not None else (action,):
        rendered = canon(part)
        for source in (remind, assist):
            obs = source.hold(loop, part, rendered)
            if obs is not None:
                return obs if part is action else {**obs, "rides": canon(action)}
    return None
