"""The CAPABILITY refusals this run has already earned — so a second identical attempt is
answered from the first refusal instead of being judged from scratch (F762).

A refused action is corrected inside the schema-retry cycle, whose micro-dialogue is dropped as
debris when the turn recovers (`engine/turndebris.py`) — so nothing of the refusal survives the
turn. Two things change, both free of a turn:

1. A first capability refusal posts ONE ENGINE NOTE: refused, permanent for this run, the
   refusal text verbatim. An engine note is not debris, so the next turn reads it.
2. A later identical attempt is answered as a REPEAT, quoting the gate's refusal whole, because
   that text already names the route out.

ONLY what `GrantPolicy.deny` refuses: config and ownership, which a run never changes about
itself, so "this cannot succeed later" is TRUE. Anything else (a schema rejection, a missing
file, a held lock) may legitimately succeed next time. `engine/schemamemo.py` owns repeated
REJECTIONS, with the opposite remedy. The absolute-path backstop (`fileops._write_gate`) needs
no memo: its refusal is the action's observation, which nothing drops.

THE VERDICT IS NEVER REPLAYED: `answer` re-asks the live policy every call, so an `allow once`
folded in mid-run makes the next attempt legal. The memo is per run and in memory; a resumed
leg starts empty, but its replayed prompt still carries the engine notes. It files no
`wrong_turn_detected` row of its own — that would double-count the schema-retry seam's.
"""

from __future__ import annotations

from . import enginenote
from .actionschema import brief_value, canon

#: The phrase every permanence claim carries — the one string tests and readers match on.
PERMANENCE_MARK = "permanent for this run"

#: At most this many durable notes per run. One per distinct refused action is the point, but a
#: run that keeps inventing refused actions must not crowd its own prompt out with notices; past
#: the cap the repeat ANSWER — the load-bearing half — still fires for every one of them.
MAX_NOTES = 5


def _memo(loop) -> dict[tuple[str, str, str], dict]:
    """This run's capability-refusal memo — (kind, target, refusal) → when and how often.

    Created on first use, like its neighbours' per-run state (`engine/schemamemo._memo`).
    """
    memo: dict[tuple[str, str, str], dict] | None = getattr(loop, "_denial_memo", None)
    if memo is None:
        memo = {}
        loop._denial_memo = memo
    return memo


def _capability_refusal(loop, action: dict, problems: list) -> str | None:
    """The refusal text when THIS problem list is the live policy's capability verdict on THIS
    action — None for anything else.

    Asked of the policy rather than read off the message, and matched against the problems the
    validator actually returned (a schema-invalid action never reaches the grant gate).
    """
    policy = getattr(loop, "grants", None)
    if policy is None or not problems:
        return None
    refusal = policy.deny(action)
    if not refusal:
        return None
    return refusal if any(str(p) == refusal for p in problems) else None


def _note(action: dict, refusal: str, turn: int) -> str:
    return (f"the action `{canon(action)}` was REFUSED at turn {turn} by a CAPABILITY check, "
            f"and that refusal is {PERMANENCE_MARK} — what it gates is the user's to switch or "
            f"nobody's to switch, never the run's, so emitting the action again on a later turn "
            f"earns the same refusal and spends the turn for nothing. Refusal, word for word: "
            f"{refusal}")


def _repeat(action: dict, refusal: str, *, first: int, attempts: int) -> str:
    return (f"REPEAT — `{canon(action)}` is an action this run ALREADY had refused at turn "
            f"{first}; this is attempt {attempts}. The refusal is a CAPABILITY check — it rests "
            f"on config or ownership, which no run changes about itself — so it is "
            f"{PERMANENCE_MARK}: no re-wording, no later turn and no retry can make this action "
            f"land. Do not emit it again — take the route the refusal itself names (below), and "
            f"carry on with the work that does not need the capability. The refusal, word for "
            f"word: {refusal}")


def answer(loop, action: dict, problems: list) -> list:
    """The problems this action's judgement should hand back, given what the run already knows.

    Unchanged for everything except a capability refusal: the first one posts its durable note,
    a later identical one comes back as the REPEAT sentence with the gate's refusal quoted whole.
    Never raises — an answer that cannot be composed must not cost the turn it was going to help.
    """
    try:
        refusal = _capability_refusal(loop, action, problems)
        if refusal is None:
            return problems
        memo = _memo(loop)
        key = (str(action.get("kind") or ""), brief_value(action), refusal)
        turn = int(getattr(loop.ctx, "turn", 0) or 0) + 1   # the turn being composed
        seen = memo.get(key)
        if seen is None:
            memo[key] = {"turn": turn, "attempts": 1}
            if len(memo) <= MAX_NOTES:
                enginenote.append(loop, _note(action, refusal, turn))
            return problems
        seen["attempts"] += 1
        return [_repeat(action, refusal, first=int(seen["turn"]),
                        attempts=int(seen["attempts"]))]
    except Exception:
        return problems
