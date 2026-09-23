"""The wording of a HELD action — the one observation that is not a dispatch result.

Split out of the flat renderer (`observations.format_observation`) when the second hold kind
arrived: the per-domain formatters (`obs_files`, `obs_library`, `obs_children`, `obs_admin`)
already establish that a family of kinds gets its own module, and a hold is now a family.

Both kinds say the same four things, because a hold the model cannot act on precisely is a
turn spent for nothing: that NOTHING ran, what the caution is, how to proceed anyway, and what
the engine wants back. Only the middle two differ by source.
"""

from __future__ import annotations

from ..reminders import LABEL_HELP, looks_too_broad

_PROCEED = ("To go ahead anyway, emit the SAME action again — it runs this time (one hold per "
            "action string per run). To avoid the consequence, do something else instead.")


def _caution(r: dict) -> str:
    """One reminder's line — plus, when its OWN tally indicts its pattern, the evidence and
    the way out, in the hold the run is already paying for.

    A reminder is only worth its turns while the consequence it names can actually happen. The
    tally that answers that was written on every fire and read by nothing: 198 holds in 21 days,
    67 of them labelled `could_not` by the run itself, and the only prune path was the model
    spontaneously remembering to look. Surfacing it HERE — where the run is re-deciding the
    action anyway and `remind` rides that same turn for free — makes pruning a thing it can do
    in the moment rather than a thing it must remember to come back for.
    """
    line = f"- [{r['id']} · {r['scope']}] {r['description']}"
    stats = r.get("stats") or {}
    if not looks_too_broad(stats):
        return line
    return (line + f"\n  ↳ THIS REMINDER'S OWN RECORD: {stats.get('fires', 0)} fires, of which "
            f"you labelled {stats.get('could_not', 0)} `could_not` — the consequence was "
            "impossible for the action it held. If that is true again here, the pattern is too "
            "broad: revise its regex or delete it with a `remind` op on this turn (it rides any "
            "action and costs no turn of its own).")


def reminder_hold(obs: dict) -> str:
    """A consequence reminder this routine wrote for itself."""
    cautions = "\n".join(_caution(r) for r in obs.get("reminders") or [])
    return (f"ACTION HELD — it did NOT run. `{obs.get('action')}` matches a consequence "
            f"reminder left for exactly this moment:\n{cautions}\n"
            f"Decide again with that in front of you. {_PROCEED}\n"
            "Then LABEL what happened: carry `remind_feedback` with the id above and one of "
            "could_not / would_have / did / didnt on the action where you know the outcome — "
            "at once if you are changing course, on the turn AFTER the held action ran if you "
            f"went ahead. {LABEL_HELP}")


def assist_hold(obs: dict) -> str:
    """A general rule the routine practises, whose moment is this action.

    The rule's own line, and the route to the rest of it: a surfaced line is deliberately
    terse, and terseness is only honest when the full text is one action away.
    """
    lines = "\n".join(f"- {line}" for line in obs.get("lines") or [])
    return (f"ACTION HELD — it did NOT run. `{obs.get('action')}` is the moment a general rule "
            f"you practise governs:\n{lines}\n"
            f"Decide again with that in front of you. {_PROCEED}\n"
            "If the rule turned out not to apply here, say so in your next `say` — that is "
            "what tells the next reader the trigger was too broad.")


#: obs kind -> renderer. `observations.format_observation` delegates on membership, so a third
#: hold kind is a line here and nothing in the flat renderer.
RENDERERS = {"reminder_hold": reminder_hold, "assist_hold": assist_hold}
