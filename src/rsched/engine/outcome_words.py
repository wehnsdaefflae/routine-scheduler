"""The OUTCOME CONTRACT a routine is created with — read from the lines its creation settled.

`create_routine` carries `produces`: what the person said the routine exists to produce, in
their words, as `config/outcome.py` wants it. One line names who the results are for, and one
line per measure names the number:

    principal: Mark
    interviews · count · higher — interviews or calls booked with a client
    weight_kg · level · lower — his weight on the scale this morning

Every route that creates a routine requires it: a routine nobody asked "what is this for,
counted how" runs on its own account of itself, and `rsched validate` fails one without it. The
person's confirmation of the draft IS the accept: scaffold writes the contract into routine.yaml
AND a `scripts/outcome.py` stub from the same lines (`workflows/outcome_stub.py`), which a later
run replaces with real counting.
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

    It names `**Work:**` because that is where the action schema's two-part summary puts the
    outcome (`engine/actionschema.py`), which `static/md.js`'s `outcomeLine` reads back as each
    console row's one-line outcome; a line demanding the summary open with the outcome would
    contradict the schema.
    """
    ids = ", ".join(m.id for m in cfg.measures)
    return ("the run's summary opens its `**Work:**` part with the outcome as "
            f"scripts/outcome.py counts it: {ids}")
