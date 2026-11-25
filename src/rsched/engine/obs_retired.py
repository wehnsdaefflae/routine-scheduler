"""Observations of kinds that are no longer actions — rendered for as long as transcripts live.

A RETIRED kind (`engine/renamedkinds.RETIRED`) cannot be emitted any more, but the records it
left behind are read for the rest of the instance's life, and by three readers that are not the
live turn loop: a RESUMED leg replays stored observations through
`observations.render_observation`, `engine/runsearch.py` renders hits from earlier transcripts,
and the console renders any run anyone opens. So this is a PERMANENT need, not a migration:
there is no date after which an old transcript stops being read, which is why these renderers
carry no expiry marker.

Here because the subject is "what a retired kind's observation says", not "what the action
does": D288 deleted the `llm` and `decide` handlers with their kinds — reaching a
model is a util capability now — and their wording would otherwise have been the one reason to
keep two modules of dead action code alive.
"""

from __future__ import annotations


def _decide_line(a: dict) -> str:
    """One answer of a recorded `decide` observation."""
    if a.get("refused"):
        return f"- {a['name']}: REFUSED by the model — no answer to this question"
    probs = a.get("probabilities") or {}
    if a["type"] == "yes_no":
        return f"- {a['name']}: P(yes) = {a['probability']:.3f}"
    spread = " · ".join(f"{k} {v:.3f}" for k, v in probs.items())
    conf = f", confidence {a['confidence']:.2f}" if a.get("confidence") is not None else ""
    if a["type"] == "choice":
        return f"- {a['name']}: {a['choice']}{conf} — {spread}"
    levels = " · ".join(f"[{i}] {k} {v:.3f}" for i, (k, v) in enumerate(probs.items()))
    return (f"- {a['name']}: score {a['score']:.2f} on 0..{max(len(probs) - 1, 1)}{conf} — "
            f"{levels}")


def format_retired(obs: dict, kind: str) -> str | None:
    """The observation text for a RECORDED `llm` or `decide` result — None for anything else.

    The words are the ones those actions' own renderers produced, unchanged: a resumed leg and
    a live turn must say the same thing about the same record (`engine/samestub.py`), and a
    transcript rendered differently after an upgrade reads as a different run.
    """
    if kind == "llm":
        if err := obs.get("error"):
            return f"OBSERVATION (llm subcall FAILED): {err}"
        return f"OBSERVATION (llm reply):\n{obs['reply']}"
    if kind == "decide":
        if err := obs.get("error"):
            return f"OBSERVATION (decide FAILED): {err}"
        images = sum(1 for f in obs.get("files") or [] if f.get("as") == "image")
        over = f" over {images} image{'s' if images != 1 else ''}" if images else ""
        head = f"OBSERVATION (decide · {obs.get('model')}{over}):"
        return "\n".join([head, *(_decide_line(a) for a in obs.get("answers") or [])])
    return None
