"""A decision's PROOF — what would have to be true for its answer to have been carried out.

The operator, 2026-10-29, answering D220 in free text: *"You need to come up with a proof at
creation and accept / decline my response based on the result of you checking the proof"*. His
fuller specification named five states and one rule that reorganizes the whole decision
lifecycle:

* `open` — an option was offered and not selected.
* `in_progress` — selected; the actions it names have not been performed yet.
* `declined` — an option that was not selected.
* `positive` — the proof exists, so the actions can be assumed performed.
* `negative` — the actions were performed and the proof is STILL absent. That is the state
  worth building the mechanism for: it flags an unexpected event, which is exactly what
  nobody notices today.

**The rule:** a decision stays open, visible and open to his feedback until at least one
option has a positive proof, or it is actively discarded or overridden. Recording his answer
is therefore no longer what settles an item — which is the defect F795 states from the other
end: *an answered decision leaves the Decisions page the moment it is recorded, and nothing
ever checks that the authorized work landed.*

And his split of the decisions a routine brings him, which is why a proof carries a CATEGORY:

* `operator_act` — an act only he can perform (a permission a routine lacks, a physical act).
  The routine must work out how to check the proof with the means it has, and may defer the
  checking to him only in extreme cases, with clear instructions.
* `routine_act` — a way forward he picks for the routine because earlier instructions were
  ambiguous or new information arrived. The acts are the routine's own, so a proof it can
  check always exists.

## Why the state is DERIVED from the per-option map, and only the map is stored

`web/api_questions` holds the principle that question state is derived, never stored twice: a
question is answered the moment `inbox/answer-<qid>.json` exists. A second stored field saying
"answered" would be a copy to keep in sync with the file that is the fact. The same applies
here one level down — so what a record stores is the AUTHORED proof per option plus the state
each option has reached, and everything a surface wants to know (`state_of`, `unproven`,
`louder_than_open`) is computed from it. One map, no derived duplicates.

## Lives at the top level, not under `web/`

`decline.py`'s reason exactly: the web layer WRITES these fields (answering, overriding) and
the engine READS them (`engine/inbox_questions` carries them onto the answer pair,
`engine/composer` renders the line the asking run reads), and `engine/` imports nothing from
`web/`.

A record that carries NO proof behaves exactly as it always did. Every ask the fleet has ever
filed is in that state, so `unproven` is False for them: this mechanism must not re-open the
instance's whole settled history on the day it ships.
"""

from __future__ import annotations

from .ids import now_iso

#: An option offered and not selected.
OPEN = "open"
#: Selected — the actions it names have not been performed yet.
IN_PROGRESS = "in_progress"
#: An option that was not selected.
DECLINED = "declined"
#: The proof exists: the actions can be assumed performed.
POSITIVE = "positive"
#: The actions were performed and the proof is still absent — an unexpected event.
NEGATIVE = "negative"

#: His two categories, and nothing else. A free vocabulary here would let a routine file
#: "unclear" and never have to say who has to act.
CATEGORIES = frozenset({"operator_act", "routine_act"})

#: The fields an option's proof must carry AT FILING. `predicate` is the computable check by
#: id, `reads` is what it reads (so a reader can tell whether the check is even possible), and
#: `category` is whose act is being proven.
REQUIRED = ("predicate", "reads", "category")

#: Which state a surface shows when the options disagree. A positive proof clears the row, so
#: it outranks everything; a negative one is the loud case; `declined` is last because an
#: option nobody chose says nothing about the decision.
_RANK = (DECLINED, OPEN, IN_PROGRESS, NEGATIVE, POSITIVE)


def problems(spec: object, options: list[str]) -> list[str]:
    """Why this per-option proof map may not be filed — empty when it may.

    Checked at FILING, because a proof invented after the answer is the thing D220 exists to
    prevent: by then the only honest reading of "did the work land" is whatever the routine
    now wishes it had promised. One complaint per option, naming the option and every field
    it is missing, so a caller fixes them in one pass.
    """
    if not isinstance(spec, dict):
        return ["`proof` must be an object keyed by the option it proves"]
    out: list[str] = []
    for option in options:
        entry = spec.get(option)
        if not isinstance(entry, dict):
            out.append(f"option {option!r} has no proof — every option needs one at filing, "
                       f"so the answer can be checked rather than assumed")
            continue
        if missing := [f for f in REQUIRED if not str(entry.get(f) or "").strip()]:
            out.append(f"option {option!r}: its proof needs {', '.join(missing)} — a "
                       f"predicate id, what it reads, and whose act it proves")
        category = str(entry.get("category") or "").strip()
        if category and category not in CATEGORIES:
            out.append(f"option {option!r}: category {category!r} is not one of "
                       f"{sorted(CATEGORIES)} — an act only the operator can perform, or "
                       f"one of this routine's own")
    if stray := sorted(set(spec) - set(options)):
        out.append(f"proof names option(s) the decision does not offer: {stray}")
    return out


def filed(spec: dict) -> dict:
    """The proof map as it is STORED on a fresh decision record: every option `open`.

    The authored spec is kept whole beside the state, so a later check knows what it was
    promised rather than re-deriving it from a predicate registry that may have moved on.
    """
    return {option: {**entry, "state": OPEN} for option, entry in spec.items()}


def answered(proof: dict, choice: str) -> dict:
    """That map after the operator answered: the chosen option `in_progress`, the rest
    `declined`.

    A choice matching no option leaves every state untouched, which is the common case and
    not an error: he answers in prose more often than he picks (D220 itself was answered that
    way), and prose selects nothing. What must never happen is prose reading as a selection —
    an item would then carry a proof nobody promised and clear itself when that proof turned
    up for an unrelated reason.
    """
    if choice not in proof:
        return {option: dict(entry) for option, entry in proof.items()}
    return {option: {**entry, "state": IN_PROGRESS if option == choice else DECLINED}
            for option, entry in proof.items()}


def _one(proof: dict, option: str) -> dict:
    """One option's entry, refusing an option the decision never offered.

    Loud on purpose: a typo in an option key would otherwise write a proof state nobody asked
    for, and the record would read as proven afterwards.
    """
    if option not in proof:
        raise KeyError(f"this decision offers no option {option!r} — its options are "
                       f"{sorted(proof)}")
    return dict(proof[option])


def checked(proof: dict, option: str, *, found: bool) -> dict:
    """That map after the periodic check ran for `option`: `positive` or `negative`.

    The check reads the world, not the record, so it is not gated on the option's prior
    state: an option he declined whose proof turns up anyway is a positive proof, and
    `state_of` is deliberately generous about which option carries it ("at least one option
    has a positive proof" — his words).
    """
    entry = _one(proof, option)
    entry["state"] = POSITIVE if found else NEGATIVE
    entry["checked_ts"] = now_iso()
    return {**proof, option: entry}


def overridden(proof: dict, option: str, *, who: str, when: str) -> dict:
    """That map after a human asserted the proof without the check finding it.

    His own words: *"an operator override that a proof has been verified"*, gated behind an
    are-you-sure step. The record says WHO and WHEN, so an overridden proof never reads as
    one the system verified — the distinction is the whole value of having states.
    """
    entry = _one(proof, option)
    entry["state"] = POSITIVE
    entry["overridden_by"] = str(who)
    entry["overridden_ts"] = str(when)
    return {**proof, option: entry}


def overridden_option(proof: dict) -> str:
    """The option whose positive state came from an override, or "" — for a surface that
    must not present a human's assertion as a check result.
    """
    for option, entry in (proof or {}).items():
        if entry.get("state") == POSITIVE and entry.get("overridden_by"):
            return str(option)
    return ""


def state_of(record: dict) -> str:
    """The decision's proof state — the highest-ranked state its options have reached.

    "" when the record carries no proof at all, which every decision filed before this
    mechanism existed does. A surface reads that as "this decision has no proof lifecycle"
    and renders exactly what it rendered before.
    """
    states = [str(entry.get("state") or "")
              for entry in (record.get("proof") or {}).values()
              if isinstance(entry, dict)]
    known = [s for s in states if s in _RANK]
    return max(known, key=_RANK.index) if known else ""


def unproven(record: dict) -> bool:
    """Was this decision ANSWERED while nothing has yet shown the work landed? (F795)

    The predicate the Decisions page needs to stop treating "recorded" as "done": True while
    an answered decision's chosen option sits `in_progress`, or came back `negative`, or the
    answer selected no option at all. False once any option is `positive` (checked or
    overridden) — and False for a record with no proof, which is every ask the engine files
    today.

    A consequence of the lifecycle rather than its centre (the builder's reading in R3064):
    it is one derived question over the stored map, so no surface has to know the rules.
    """
    return bool(record.get("answered")) and state_of(record) in (OPEN, IN_PROGRESS, NEGATIVE)


def louder_than_open(record: dict) -> bool:
    """Must this row render LOUDER than an unanswered question?

    True only for `negative`: the actions were performed and the proof is still absent, which
    he asked to be surfaced as an unexpected event. `in_progress` is the ordinary waiting
    state and `positive` clears the row, so neither shouts.
    """
    return state_of(record) == NEGATIVE


def carry(pair: dict, record: dict) -> dict:
    """Copy the proof map from a decision RECORD onto the answer pair a run consumes.

    Exactly how the decline verdict and the patch receipt already ride a pair
    (`decline.carry`, `patchreceipt.carry`): the pair is what both delivery seams read — the
    boot digest and the live turn boundary — so a fact missing here reaches no run at all.
    Absent when the decision carried no proof, so an ordinary answer gains nothing and no
    reader learns to expect it.
    """
    if isinstance(record.get("proof"), dict) and record["proof"]:
        pair["proof"] = record["proof"]
    return pair


def _awaiting(pair: dict) -> dict:
    """The option this answer selected and whose proof is still outstanding, or {}."""
    for entry in (pair.get("proof") or {}).values():
        if isinstance(entry, dict) and entry.get("state") in (IN_PROGRESS, NEGATIVE):
            return entry
    return {}


def digest_line(pair: dict) -> str:
    """The line the asking RUN reads under its answer, or "" — what will be checked.

    Written for the failure it prevents: a run reads "yes", records the item as closed and
    plans on it, when the decision is settled only once the proof turns up. So the sentence
    names the predicate and what it reads, and says the answer is conditional.
    """
    entry = _awaiting(pair)
    if not entry:
        return ""
    found = entry.get("state") == NEGATIVE
    return (f"proof: this answer is CONDITIONAL — `{entry.get('predicate')}` over "
            f"{entry.get('reads')} is what shows the work landed, and the decision stays "
            f"open until it does"
            + (". It was checked and came back ABSENT: say what happened before building on "
               "the answer" if found else ""))


def digest_suffix(pair: dict) -> str:
    """`digest_line` as an indented continuation line, or "" — for a renderer appending it
    under an existing `A:` line (the state digest, the mid-run answer injection).
    """
    line = digest_line(pair)
    return f"\n  {line}" if line else ""
