"""The OUTCOME CONTRACT a routine is created with — read from the lines its creation settled.

`create_routine` carries `produces`: what the person said the routine exists to produce, in
their words, as `config/outcome.py` wants it. One line names who the results are for, and one
line per measure names the number:

    principal: Mark
    interviews · count · higher — interviews or calls booked with a client
    weight_kg · level · lower — his weight on the scale this morning

Every route that creates a routine requires it — a conversation's confirmed draft, a scheduled
run's queued proposal and the Decisions page that materializes one — because a routine created
without it is one `rsched validate` fails at once, and the freelance-radar lesson is that a
routine nobody asked "what is this for, counted how" runs for months on its own account of
itself. The person's confirmation of the draft IS the accept: scaffold writes the contract into
the new routine.yaml, and the routine's first run writes `scripts/outcome.py` from the records
its stages keep (the recipe's Done-when line names the measures; workflows/pipeline.py).
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from ..config.outcome import OutcomeConfig

__all__ = ["FORM", "OutcomeConfig", "contract", "done_when_line", "refusal"]

FORM = ("`principal: <who the results are for>` once, then one line per measure: "
        "`<id> · <count|level> · <higher|lower> — <what it counts, in their words>`")

_PRINCIPAL = re.compile(r"^\s*principal\s*:\s*(.+?)\s*$", re.IGNORECASE)
_MEASURE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*[·|]\s*(count|level)\s*[·|]\s*(higher|lower)"
                      r"\s*(?:—|–|-{1,2})\s*(.+?)\s*$", re.IGNORECASE)


def contract(entries: list[str]) -> OutcomeConfig | str:
    """The contract the lines declare, or ONE sentence naming what is wrong with them."""
    if not entries:
        return f"no outcome was given — {FORM}"
    principal, measures = "", []
    for line in entries:
        if m := _PRINCIPAL.match(line):
            if principal:
                return "two `principal:` lines — the results are for one principal"
            principal = m[1]
        elif m := _MEASURE.match(line):
            measures.append({"id": m[1].lower(), "kind": m[2].lower(),
                             "better": m[3].lower(), "label": m[4]})
        else:
            return f"{line!r} is in neither form — {FORM}"
    try:
        return OutcomeConfig.model_validate({"principal": principal, "measures": measures})
    except ValidationError as exc:
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'outcome'}: {e['msg']}"
                         for e in exc.errors())


def refusal(slug: str, why: str) -> dict:
    """create_routine's observation for a contract that is absent or malformed."""
    return {"kind": "create_routine", "slug": slug, "rejected": True,
            "reason": f"the outcome contract is not settled ({why}). Nothing was created. Ask "
                      "the user what this routine exists to PRODUCE and for whom, counted how "
                      "— an ask_user with options — and carry their answer in `produces`."}


def done_when_line(cfg: OutcomeConfig) -> str:
    """The recipe's Done-when line for the contract: it names every measure as a word, which
    is what the setup surface checks (readmodels/surface_outcome.names_a_measure).
    """
    ids = ", ".join(m.id for m in cfg.measures)
    return f"the run's summary leads with the outcome as scripts/outcome.py counts it: {ids}"
