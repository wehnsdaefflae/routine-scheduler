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

#: A conversation that spent at least this many turns did work worth generalizing. Turns are
#: the one effort measure every run writes (status.json's `turn`, read through registry), they
#: survive a resume, and they do not grow with a chatty reply the way a message count does.
#: Eight is the smallest count that excludes the shapes we do NOT want to offer on: a one-reply
#: question, a correction, an aborted start.
SUBSTANTIAL_TURNS = 8


def offer(info, *, bound_playbook: str | None) -> dict:
    """The `playbook_offer` block of a conversation's detail payload.

    Keys: `show` (bool — the console renders the offer iff true), `reason` (one line for the
    operator, naming the measurement, never empty) and `turns`/`threshold` so the reason can be
    read back without recomputing it.

    `bound_playbook` is the conversation's own source playbook (`cfg.playbook_slug`): a
    conversation SEEDED from a playbook is offered the refinement (PUT) rather than a second
    near-duplicate (POST), which is what `mode` says.
    """
    run = latest_with_summary(info)
    turns = int(getattr(run, "turn", 0) or 0) if run is not None else 0
    finished = bool(run is not None and (getattr(run, "summary", "") or "").strip())
    mode = "update" if bound_playbook else "save"
    if run is None:
        return {"show": False, "mode": mode, "turns": 0, "threshold": SUBSTANTIAL_TURNS,
                "reason": "this conversation has not run yet"}
    if not finished:
        return {"show": False, "mode": mode, "turns": turns, "threshold": SUBSTANTIAL_TURNS,
                "reason": "the conversation is still working — the offer waits for a reply"}
    if turns < SUBSTANTIAL_TURNS:
        return {"show": False, "mode": mode, "turns": turns, "threshold": SUBSTANTIAL_TURNS,
                "reason": (f"{turns} turn{'s' if turns != 1 else ''} so far — a playbook is "
                           f"offered from {SUBSTANTIAL_TURNS}")}
    what = (f"refine “{bound_playbook}” with what this conversation did differently"
            if bound_playbook else "distil the procedure that satisfied it into a playbook")
    # "turns", not "turns of work": the console leads with "This conversation did real
    # work — ", so 0.443.0 rendered "did real work — 23 turns of work — distil…", saying
    # work twice. Found by RENDERING the row; the source read fine.
    return {"show": True, "mode": mode, "turns": turns, "threshold": SUBSTANTIAL_TURNS,
            "reason": f"{turns} turns — {what}"}
