"""The CAPABILITY refusals this run has already earned — so a second identical attempt is
answered from the first refusal instead of being judged from scratch (F762).

MEASURED: `vodafone-outage-claims:20261023-024513` re-emitted a write to its own `main.md` at
turns 17, 18, 19 and 34; `moltbook-heartbeat:20261023-172052` at turns 34 and 35. Every attempt
met the same gate (`grantpolicy.GrantPolicy.deny`, the recipe clause), whose text already names
the right route — so the wording was never the defect. The defect is that NOTHING OF THE
REFUSAL SURVIVES THE TURN: a refused action is corrected inside the schema-retry cycle and that
whole micro-dialogue is dropped as debris the moment the turn recovers
(`engine/turndebris.py`), so the next turn's prompt holds the run's own reasoning and no
refusal at all. The specimen proves it in its own voice — at turn 18 it wrote *"I have to
correct myself … I never attempted one"*, then attempted it again, twice.

TWO THINGS CHANGE, both free of a turn:

1. A first capability refusal posts ONE ENGINE NOTE (`engine/enginenote.py`, the engine's one
   voice): this action was refused, the refusal is permanent for this run, and the refusal text
   verbatim. An engine note is not debris, so the turn AFTER the refusal reads it — which is
   the only surface the measured specimens lacked.
2. A later identical attempt is answered as a REPEAT: the turn it was first refused at, the
   attempt number, that no re-wording or later turn can make it land, and the gate's own
   refusal quoted whole, because that text already names the route out (propose the change at
   finish, or file the typed access request).

WHY ONLY WHAT `GrantPolicy.deny` REFUSES. What that gate answers is config and ownership — a
capability the user switches, or a structural seal (routine.yaml, runs/) nobody switches at all
— and a run changes neither of those about itself. So its verdict is deterministic and holds for
the whole run: the one class of refusal where "this cannot succeed later" is TRUE. A schema
rejection, a missing field, a file that is not written yet, a lock held elsewhere: each can
legitimately succeed on the next attempt, and telling a run otherwise would stop work that was
about to land. So the memo fires on exactly one predicate — the live policy's own `deny()` is
what produced this problem list — and never on the shape of a message. F736
(`engine/schemamemo.py`) owns the other half, repeated REJECTIONS, and its remedy is the
opposite one (rebuild the object, shed the grammar), which is why these are two modules.

NOT THIS SEAM: `engine/fileops._write_gate`, the backstop that catches the same recipe write
addressed by ABSOLUTE path. That refusal is already the action's OBSERVATION — it is what the
run reads next, with nothing dropping it — so it needs no memo to survive the turn. Only the
validation-seam form (`GrantPolicy.deny`, the relative-path one the measured specimens used)
vanished with the debris.

THE VERDICT IS NEVER REPLAYED. `answer` re-asks the live `GrantPolicy` on every call and does
nothing at all when it permits the action: a one-run `allow once` folded in mid-run
(`GrantPolicy.with_overlay`) must make the very next attempt legal. The memo only ever enriches
a refusal the policy has just issued again — it cannot manufacture one.

THE MEMO IS PER RUN AND IN MEMORY, keyed on (action kind, target, refusal text), like the
rejection memo beside it. A resumed leg starts empty on purpose — but its replayed prompt still
carries the engine notes verbatim (`engine/history.replay_messages`), so what the run was told
is not lost with the dict.

NOT IN SCOPE, deliberately: this files no `wrong_turn_detected` row of its own. The repeat is
already readable back from the transcript (the refusal text the run was handed carries the
REPEAT sentence), and a second reason filed for one turn would double-count a turn the
schema-retry seam has already counted (`engine/wrongturn.py` keeps one row per reason+turn).
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

    Created on first use, the idiom its neighbours already use for per-run state
    (`engine/schemamemo._memo`, `loop._schema_off`): `engine/loop.py` sits at its size ceiling.
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
    validator actually returned: a schema-invalid action is rejected before the grant gate is
    consulted (`engine/actions.validate_action` short-circuits), so its problems are not this
    module's business even when the action would also have been refused.
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
