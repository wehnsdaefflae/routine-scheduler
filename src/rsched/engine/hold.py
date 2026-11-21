"""The pre-execution HOLD seam — one interruption per action, whatever asked for it.

Two layers can stop an action before it runs: a CONSEQUENCE REMINDER (`engine/remind.py`) and a
RULE ASSIST (`engine/assist.py`). This is the one place either reaches the turn loop, because:

**The ledger key carries the SOURCE.** Re-emitting a held action IS the confirmation to
proceed; keyed on the bare string, one layer would spend the other's one allowed hold. A
REMINDER holds once per reminder id (a reworded action re-held by a caution already in context
teaches nothing); the RULE layer once per action string.

**One interruption per action.** However many sources match, the model is stopped once — the
anti-livelock reasoning applies to the PAIR. Precedence is specific-before-general: a reminder
(what THIS routine learned) before a rule (a standing principle).

"Already held" means within the GUARD SCOPE (engine/guardscope.py). A resumed leg rebuilds the
ledger from the hold observations its transcript recorded: the kind names the source,
`reminders` the ids a reminder hold spent, `action` the string a rule hold spent.
"""

from __future__ import annotations

from .actionschema import canon

#: Every observation kind that means "the action did not run", with the SOURCE whose ledger
#: entry it records. `is_hold` and the ledger's rebuild read this one table, so a third hold
#: kind is a row here.
HOLD_SOURCES = {"reminder_hold": "reminder", "assist_hold": "rule"}
HOLD_KINDS = frozenset(HOLD_SOURCES)


def configure(loop) -> None:
    """This seam's run state: which (source, key) pairs have already held — the key is a
    reminder id for the reminder layer, the canonical action string for the rule layer.
    """
    loop.holds = set()


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed the ledger from the holds recorded inside the guard scope (engine/guardscope.py)
    — a resumed leg's counterpart of `configure`. A hold needs no extra record: its observation
    is the hold, and it already names what it spent (`hold_keys`).
    """
    loop.holds = set()
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "observation" and is_hold(payload):
            source = HOLD_SOURCES[payload["kind"]]
            loop.holds.update((source, key) for key in hold_keys(payload))


def hold_keys(obs: dict) -> list[str]:
    """What one recorded hold spent in its source's ledger: the reminder ids it listed, or the
    action string a rule hold stopped.
    """
    if obs.get("kind") == "reminder_hold":
        return [str(r.get("id") or "") for r in obs.get("reminders") or []]
    return [str(obs.get("action") or "")]


def is_hold(obs: dict) -> bool:
    """Did this observation report a HELD action — one the engine did not execute?

    The predicate behind two consequences that must stay in step: a held action grounds no
    finish (`executed_actions`, live and rebuilt on resume) and spends no allow-once grant
    (spent by USE, not by attempt).
    """
    return bool(obs) and obs.get("kind") in HOLD_KINDS


def held_before(loop, source: str, key: str) -> bool:
    """Has THIS source already held on THIS key (a reminder id, a rule hold's action string)
    in this guard scope — this run, or this reply of a conversation?
    """
    return (source, key) in loop.holds


def mark_held(loop, source: str, key: str) -> None:
    loop.holds.add((source, key))


def before_dispatch(loop, action: dict) -> dict | None:
    """Ask each source, in precedence order, for a reason to hold this action.

    Returns the observation the model reads INSTEAD of the action's result, or None to let it
    execute. The canonical string is computed once here and handed to every source, so the two
    layers can never disagree about what the action was.

    A write carrying a script (`then_script`, engine/thenscript.py) is asked about TWICE: as
    itself, then as the script alone. A held script holds the whole action, and the
    observation names what it rode on.
    """
    from . import assist, remind, thenscript

    script = thenscript.riding(action)
    for part in (action, script) if script is not None else (action,):
        rendered = canon(part)
        for source in (remind, assist):
            obs = source.hold(loop, part, rendered)
            if obs is not None:
                loop.ctx.holds += 1      # a quality signal (engine/runrecord.py)
                return obs if part is action else {**obs, "rides": canon(action)}
    return None
