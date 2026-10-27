"""SEVERAL questions in ONE `ask_user` action — the fan-out (D212).

Its own module for the reason `engine/autoask.py` is one: `engine/interact.py` is the ask
protocol — turning a run's need for a human into ONE decision record and back into an answer —
and it sits exactly on the repo's size bar. Asking several at once is a concern beside that,
not inside it, and it is built ON that seam rather than beside it: every record is filed by
`interact.handle_ask`, so a batch's cards are byte-identical to the ones a run has always
filed, plus the `batch` id that says they were asked together.

WHY, measured over the 282 question records the instance had filed before this existed:

* 37% of runs (60 of 163) already asked more than one question, up to 6 in a single run —
  while the ONLY way to ask was one action per question. Five questions cost a run five turns
  of its own context and the operator five unrelated cards.
* He already answers in batches: 51% of his answering sittings disposed of several questions,
  a third of them across several routines, at a median of 11 minutes. The asking side was the
  half that did not match.
* 174k tokens of question prose sit in that store (median 430 tokens per ask), so the saving
  is real context, not tidiness.

What a batch is NOT is a waiting mode. `mode: "blocking"` keeps its own meaning — the run's
process sits out the ask timeout — and is refused on a batch (`askfields`), because parking a
run on the first of five answers is not a thing a run could want: 0 of those 282 asks used it,
and 0% of answers arrived within an hour (median 2.97 h). A batch files its records and the
run carries on. Which of the questions STOPPED the run's work is the records' CLASS
(`askclass`), a recorded fact on each card, not a suspended process.
"""

from __future__ import annotations

from ..ids import batch_id
from . import askclass, interact

#: The single-question fields a batch carries per question instead of once per action.
_PER_QUESTION = ("options", "default", "request", "config_patch", "blocks_work")


def _one(spec: dict) -> dict:
    """One entry of the batch as the single-question `ask_user` action it is filed as."""
    return {"kind": "ask_user", "mode": "deferred",
            "question": str(spec.get("question") or ""),
            **{f: spec[f] for f in _PER_QUESTION if spec.get(f) is not None}}


def handle(loop, action: dict, poll_s: float) -> dict:
    """File one decision record per question, all sharing a batch id; never wait.

    The per-question observations are kept whole in `filed`, so the run can see which record
    each question became (and any that was REFUSED before it filed anything — a `config_patch`
    naming a routine that does not exist, say — without that refusal costing the others).
    """
    ctx = loop.ctx
    bid = batch_id(ctx.run_ts, ctx.turn)
    filed: list[dict] = []
    for spec in action.get("questions") or []:
        one = _one(spec if isinstance(spec, dict) else {})
        obs = interact.handle_ask(loop, one, poll_s=poll_s, batch=bid)
        filed.append({**{k: v for k, v in obs.items() if k != "kind"},
                      "question": one["question"],
                      "class": askclass.of(request=obs.get("request"),
                                           blocks_work=one.get("blocks_work"))})
    return {"kind": "ask_user", "batch": bid, "mode": "deferred",
            "count": len(filed), "filed": filed,
            "blocking": sum(1 for f in filed if f["class"] == askclass.BLOCKING)}
