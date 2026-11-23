"""Whether a conversation should be OFFERED a distilled playbook, and why (D175).

The operator answered "do we still need playbooks?" with option (c), 2026-10-10: *"Keep it and
make it earn its place: the conversation finish flow OFFERS to distil a playbook when a
conversation did substantial work, so the corpus grows from use rather than from intent. Bets
that the feature was never used because nothing ever suggested it."*

The bet is the point, so this module is written to make it FALSIFIABLE: the offer appears on a
conversation that did real work, and if nothing is ever distilled anyway, the retirement case
becomes evidence instead of inference (the measurement that opened the row: the library holds
exactly ONE playbook, created 2026-07-14, referenced by nothing).

An OFFER, not a nag. The decision lives here rather than in the console so that what counts as
"substantial" is one testable predicate instead of a number in JavaScript, and so a second
surface (a future digest, a notification) asks the same question of the same reader.
"""

from __future__ import annotations

from ..readmodels.summaries import latest_with_summary
from ..registry import TERMINAL_STATES

#: A conversation that spent at least this many turns did work worth generalizing. Turns are
#: the one effort measure every run writes (status.json's `turn`, read through registry), they
#: survive a resume, and they do not grow with a chatty reply the way a message count does.
#: Eight is the smallest count that excludes the shapes we do NOT want to offer on: a one-reply
#: question, a correction, an aborted start.
SUBSTANTIAL_TURNS = 8


def offer(info, *, bound_playbook: str | None) -> dict:
    """The `playbook_offer` block of a conversation's detail payload.

    Keys: `show` (bool — the console renders the offer iff true), `why` (WHICH reason made it
    true, so the console knows WHERE to render: `turns` beside the composer, `final` under the
    last reply, "" when nothing is offered), `reason` (one line for the operator, naming the
    measurement, never empty) and `turns`/`threshold` so the reason can be read back without
    recomputing it.

    TWO reasons, one predicate, exactly one surface. The turn count (`SUBSTANTIAL_TURNS`) asks
    beside the composer of a conversation that did real work and may yet do more; the reply's
    own `final` declaration asks under that reply, where a person has just read "this is done".
    They are checked in that order of strength — final first — and `why` names the winner, so a
    conversation that satisfies both is asked ONCE, at its ending, rather than in two places.

    THE `reason` CONTRACT DIFFERS BY `why`, because the two surfaces compose different
    sentences. `turns` completes playbook-row.js's clause (see below); `final` completes
    final-offer.js's "This conversation is done — ", so it is an IMPERATIVE fragment ("save it
    as a playbook") that the console renders with its own question mark. Each is guarded by the
    browser case that renders it: tests/ui/test_playbook_offer_sentence.py and
    tests/ui/test_final_offer.py.

    THE `reason` CONTRACT WHEN `show` IS TRUE: it is a sentence FRAGMENT that COMPLETES the
    console's clause — components/playbook-row.js renders "This conversation did real work —
    {reason}?". So: no subject, no leading capital, no terminal punctuation, and no word of the
    lead-in repeated (0.443.0 said "work" twice; the refine wording said "this conversation
    did" twice). The sentence exists in neither file, so the guard that holds this is the
    browser case tests/ui/test_playbook_offer_sentence.py, which reads the composed line; the
    unit tests below cannot see the console's half. The three `show: False` reasons are
    STANDALONE sentences instead — nothing renders them today, and reconciling the two grammars
    is a separate decision (R3645).

    `bound_playbook` is the conversation's own source playbook (`cfg.playbook_slug`): a
    conversation SEEDED from a playbook is offered the refinement (PUT) rather than a second
    near-duplicate (POST), which is what `mode` says.
    """
    run = latest_with_summary(info)
    turns = int(getattr(run, "turn", 0) or 0) if run is not None else 0
    finished = bool(run is not None and (getattr(run, "summary", "") or "").strip())
    mode = "update" if bound_playbook else "save"
    if run is None:
        return {"show": False, "why": "", "mode": mode, "turns": 0,
                "threshold": SUBSTANTIAL_TURNS,
                "reason": "this conversation has not run yet"}
    if not finished:
        return {"show": False, "why": "", "mode": mode, "turns": turns,
                "threshold": SUBSTANTIAL_TURNS,
                "reason": "the conversation is still working — the offer waits for a reply"}
    if _is_final(run):
        # The SECOND reason, and the stronger one: the reply declared itself final and the
        # conversation is terminal, so this is the conversation's own ending — the moment the
        # procedure is complete and nothing more is coming. It overrides the turn count in both
        # directions: a short conversation that ENDED is offered, and a long one that ended is
        # offered HERE rather than beside the composer, which is what `why` tells the console.
        what = (f"update “{bound_playbook}” with how it went"
                if bound_playbook else "save it as a playbook")
        return {"show": True, "why": "final", "mode": mode, "turns": turns,
                "threshold": SUBSTANTIAL_TURNS, "reason": what}
    if turns < SUBSTANTIAL_TURNS:
        return {"show": False, "why": "", "mode": mode, "turns": turns,
                "threshold": SUBSTANTIAL_TURNS,
                "reason": (f"{turns} turn{'s' if turns != 1 else ''} so far — a playbook is "
                           f"offered from {SUBSTANTIAL_TURNS}")}
    # "what went differently here", not "what this conversation did differently": the console
    # leads with "This conversation did real work — ", so the refine wording repeated THREE of
    # its words. Same defect as the 0.443.0 one below, in the other positive branch; found by
    # tests/ui/test_playbook_offer_sentence.py, which reads the COMPOSED sentence (R3645).
    what = (f"refine “{bound_playbook}” with what went differently here"
            if bound_playbook else "distil the procedure that satisfied it into a playbook")
    # "turns", not "turns of work": the console leads with "This conversation did real
    # work — ", so 0.443.0 rendered "did real work — 23 turns of work — distil…", saying
    # work twice. Found by RENDERING the row; the source read fine.
    return {"show": True, "why": "turns", "mode": mode, "turns": turns,
            "threshold": SUBSTANTIAL_TURNS, "reason": f"{turns} turns — {what}"}


def _is_final(run) -> bool:
    """Did this reply declare itself FINAL, with the conversation terminal?

    Both halves are required and they answer different questions. `final` is the reply's own
    claim that it delivered what was asked (the finish action's field, carried on
    registry.RunInfo); the run STATE is whether anything is still happening — a reply that
    declared itself final and was then resumed by a further message is live again, and a
    conversation that is still working has not ended whatever the last leg claimed.
    """
    return (getattr(run, "final", None) is True
            and str(getattr(run, "state", "")) in TERMINAL_STATES)
