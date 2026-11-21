"""The `do_nothing` action (D186): a run states that nothing was due, and WHY.

Records "nothing was due" as a turn, so "the sources were empty" and "the sources were never
read" no longer look identical in a transcript. No fields: the reasoning is `say`, which
`actions.validate_action` requires non-empty for this kind alone.

IT GROUNDS NOTHING. `loop.executed_actions` counts what could ground a top-level `finish(ok)`
against the fabrication guard (engine/finishgate.py); counting `do_nothing` would let any run
pass the guard by writing one word. `grounds_a_finish` is the one predicate, asked by the live
loop and by the resume rebuild alike.
"""

from __future__ import annotations

#: Observation kinds that dispatched but establish nothing a finish could rest on (a hold,
#: owned by `engine/hold.py`, never dispatched at all). `outcome_unknown`
#: (`engine/unknownoutcome.py`) records a result that was NEVER KNOWN.
NON_GROUNDING_KINDS = frozenset({"do_nothing", "outcome_unknown"})


def grounds_a_finish(obs: dict) -> bool:
    """May this observation ground a top-level `finish(ok)`?

    False for an action the engine did not run (a hold) and for one whose whole content is a
    statement about there being nothing to run (`do_nothing`). The live loop and the resume
    rebuild both ask this one predicate, so the counter means the same in both.
    """
    from .hold import is_hold

    return bool(obs) and not is_hold(obs) and obs.get("kind") not in NON_GROUNDING_KINDS


def record_grounding(loop, action: dict, obs: dict) -> None:
    """The LIVE counterpart of `grounding_from`: count this observation and remember its KIND.

    Called from `engine/loop._land`; the live and resume rules live together here. A `rejected`
    observation (the reserved finish turn's refusal) ran nothing, so its kind may not back a
    claim.
    """
    loop.executed_actions += 1
    if not obs.get("rejected"):
        loop.executed_kinds.add(action.get("kind", "?"))


def grounding_from(events) -> tuple[int, set[str]]:
    """What a transcript's observations ground: (how many, which KINDS) — D243.

    The resume's counterpart to `record_grounding`. The KINDS half feeds the claim guard
    (`engine/finish_guard.unbacked_action_claims`), which would otherwise reject every claim the
    earlier leg's actions back. A `rejected` observation counts for neither.
    """
    count, kinds = 0, set()
    for e in events:
        if e.get("type") != "observation":
            continue
        payload = e.get("payload") or {}
        if payload.get("rejected") or not grounds_a_finish(payload):
            continue
        count += 1
        if kind := payload.get("kind"):
            kinds.add(kind)
    return count, kinds


def handle(action: dict) -> dict:
    """Record the statement. The reasoning is echoed back so the run reads what it just
    recorded rather than a bare acknowledgement.
    """
    return {"kind": "do_nothing", "reasoning": str(action.get("say") or "").strip()}
