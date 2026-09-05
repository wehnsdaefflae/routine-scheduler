"""The wording of a HELD action — the one observation that is not a dispatch result.

Split out of the flat renderer (`observations.format_observation`) when the second hold kind
arrived: the per-domain formatters (`obs_files`, `obs_library`, `obs_children`, `obs_admin`)
already establish that a family of kinds gets its own module, and a hold is now a family.

Both kinds say the same four things, because a hold the model cannot act on precisely is a
turn spent for nothing: that NOTHING ran, what the caution is, how to proceed anyway, and what
the engine wants back. Only the middle two differ by source.
"""

from __future__ import annotations

from ..reminders import LABEL_HELP

_PROCEED = ("To go ahead anyway, emit the SAME action again — it runs this time (one hold per "
            "action string per run). To avoid the consequence, do something else instead.")


def reminder_hold(obs: dict) -> str:
    """A consequence reminder this routine wrote for itself."""
    cautions = "\n".join(f"- [{r['id']} · {r['scope']}] {r['description']}"
                         for r in obs.get("reminders") or [])
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
