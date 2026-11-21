"""SEVERAL questions in ONE `ask_user` action — the fan-out (D212).

Built ON the ask protocol (`engine/interact.py`), not beside it: every record is filed by
`interact.handle_ask`, so a batch's cards are identical to single asks plus the `batch` id that
says they were asked together. One action instead of one turn and one unrelated card per
question.

A batch is NOT a waiting mode: `mode: "blocking"` is refused on a batch (`askfields`), since
parking a run on the first of several answers is not something a run could want. A batch files
its records and the run carries on; which questions STOPPED the work is each record's CLASS
(`askclass`).
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
    # `count` (and `blocking`) is what REACHED his page: a question REFUSED before it filed
    # anything was never asked, so refusals are named apart and cannot read as success.
    refused = [{"question": f["question"], "error": f["error"]} for f in filed if f.get("error")]
    # The note travels WITH the observation: what a refusal means (nothing was asked, re-file
    # this turn) is this module's own fact, not the renderer's.
    note = "".join(f" {len(refused)} REFUSED, so {'it is' if len(refused) == 1 else 'they are'} "
                   "on NO surface of his and nobody was asked — re-file this turn: "
                   + "; ".join(r["error"] for r in refused) if refused else "")
    return {"kind": "ask_user", "batch": bid, "mode": "deferred",
            "count": sum(1 for f in filed if not f.get("error")), "filed": filed,
            **({"refused": refused, "refused_note": note} if refused else {}),
            "blocking": sum(1 for f in filed
                            if f["class"] == askclass.BLOCKING and not f.get("error"))}
