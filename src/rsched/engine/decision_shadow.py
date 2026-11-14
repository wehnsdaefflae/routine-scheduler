"""A decision model answers the SAME typed question as the chat model, and both are recorded.

D287, answered by the operator 2026-11-14: *"Reroute the refusal classifier AND cascade the
finish verifier: a typed screen over each claim, with the prose judge called only on the claims
it flags"*. This is step 1 of that, and it REROUTES NOTHING — deliberately.

WHY THE MEASUREMENT IS THE FIRST INCREMENT, not a preamble to it. Every option the operator was
offered carried the clause *"measured in parallel before it replaces anything"*, because of what
the decision row measured on this instance's own models: on an underdetermined question,
permuting the options moved `jev`'s probability mass by about 10 points while the argmax never
moved. A system that reads only the verdict sees perfect consistency and learns nothing about
the instability underneath. Routing a live classification to an instrument with that property,
on the evidence that it is cheaper, is how a $0.0001 call costs a run its correctness.

AND HIS OTHER CONSTRAINT GOVERNS THIS PATH ALREADY. `engine/refusal.py` quotes him from
2026-08-22, after a marker-list approach had failed: *"only an LLM classification subcall
(tool_call model, schema'd verdict) may decide the non-obvious cases either way"*. So the chat
model keeps deciding here. The decision model answers beside it; the pair goes into a stream;
the swap becomes a decision someone can take on numbers rather than on a price list.

WHAT IT COSTS TO RUN: one decision call per classification — $0.0001 for all 22 of last month's
decision calls together, against $11.37 for 191 chat subcalls — and no added latency on the
path that matters, because the chat verdict is already in hand when the comparison is made.

THE STREAM IS DURABLE AND INSTANCE-WIDE (`.control/decision-shadow.jsonl`), following
`workflow-usage.jsonl`: run dirs fall to retention and the question here is whether two models
agree over HUNDREDS of calls. Each row carries both verdicts, the probability and the threshold
it was scored against — the probability because the argmax is exactly what hid the instability,
and the threshold because the first question a reader of this stream will ask is what a
different one would have decided.

FAILURE DISCIPLINE: a measurement that can fail a run is worse than no measurement. Every
failure mode — no decision model configured, a provider error, a malformed answer, an unwritable
stream — returns the chat model's verdict untouched. An unanswered comparison records
`decision_verdict: null` and `agree: null`, never `false`: an absent verdict and a negative
verdict are different facts, and conflating them would make the stream lie in the direction that
looks safe.
"""

from __future__ import annotations

import logging

from ..endpoints.decisions import (
    DecisionQuestion,
    decide,
    default_decision_model,
    evidence_text,
    pick_decision_model,
)
from ..ids import now_iso
from ..paths import append_jsonl

log = logging.getLogger("rsched.engine.decision_shadow")

#: The durable instance-wide stream, in `.control/` beside the usage and health streams.
SHADOW_FILE = "decision-shadow.jsonl"

#: The two sites the operator's answer names: `refusal · classify reply` (34 calls/month, 3.7 s)
#: and `finish · verify claims` (184 calls, 24.5 s, $11.15 — all at the moment a run is ending).
#: A third site would be scope this decision does not carry.
SITES = ("refusal", "finish")

#: P(yes) at or above this reads as a yes. 0.5 is the only threshold that is not a tuning
#: choice, and it is recorded in every row so a later reader can rescore the whole stream.
THRESHOLD = 0.5

_TIMEOUT_S = 20


def _ask_many(ctx, questions: list[DecisionQuestion], evidence: str):
    """Put SEVERAL typed questions over one evidence to this routine's decision model — the
    transport boundary of this module. Raises on any transport failure; `compare` and
    `compare_claims` are the ones that may not.

    One seam for both forms, so what is measured is one code path: a second resolution of the
    endpoint would be a second place for the role order to drift from `pick_decision_model`'s.
    """
    endpoint, ref = pick_decision_model(ctx.server, None, images=False,
                                        roles=getattr(ctx.routine, "models", None))
    purpose = ("shadow · verify claims" if len(questions) > 1 or questions[0].name == "finish"
               else f"shadow · {questions[0].name}")
    return decide(endpoint, ref, evidence, [], questions, timeout=_TIMEOUT_S, purpose=purpose)


def _ask(ctx, question: str, evidence: str, *, name: str):
    """One typed question — the single-site form, through the same seam."""
    return _ask_many(ctx, [DecisionQuestion(name=name, type="yes_no", question=question)],
                     evidence)


def _available(ctx) -> bool:
    """Is there a decision model to compare AGAINST? Read the same way the engine resolves one
    (`default_decision_model`: the routine's own role first, then the instance default), so the
    measurement never asks a question the real route would not have asked.
    """
    server = getattr(ctx, "server", None)
    if server is None or not getattr(server, "decision_models", None):
        return False
    try:
        return bool(default_decision_model(server, getattr(ctx.routine, "models", None),
                                           images=False))
    except Exception:                             # a measurement never raises
        return False


def compare(ctx, *, site: str, question: str, evidence: str | dict | list,
            chat_verdict: bool, name: str = "") -> bool:
    """Record what a decision model would have said, and return `chat_verdict` UNCHANGED.

    The return value is the contract: this function is an observer. Callers may wrap their
    existing verdict in it without changing what the run does — which is what makes the
    measurement safe to ship ahead of the decision it informs.
    """
    if not _available(ctx):
        # An instance that configured no decision model would otherwise write one identical
        # "not configured" row per classification — noise, and it would read in the fold as a
        # disagreement rate nobody can interpret. Nothing to compare, nothing to record.
        return chat_verdict
    verdict: bool | None = None
    probability: float | None = None
    model = ""
    usage: dict = {}
    error = ""
    try:
        answered = _ask(ctx, question, evidence_text(evidence), name=name or site)
        model = answered.model or ""
        usage = answered.usage or {}
        answer = answered.answers[0] if answered.answers else None
        probability = getattr(answer, "probability", None) if answer is not None else None
        if probability is not None:
            verdict = probability >= THRESHOLD
    except Exception as exc:                      # every failure mode: see the module docstring
        error = str(exc)[:300]
        log.info("decision shadow at %s: %s", site, error)
    row = {
        "ts": now_iso(),
        "site": site,
        "routine": getattr(getattr(ctx, "routine", None), "slug", ""),
        "run_id": getattr(ctx, "run_id", ""),
        "chat_verdict": chat_verdict,
        "decision_verdict": verdict,
        # None, never False: an unanswered comparison is not an agreement
        "agree": None if verdict is None else verdict == chat_verdict,
        "probability": probability,
        "threshold": THRESHOLD,
        "model": model,
        "usage": usage,
    }
    if error:
        row["error"] = error
    if verdict is None and not error:
        row["error"] = "the answer carried no probability"
    _record(ctx, row)
    return chat_verdict


def compare_claims(ctx, *, question: str, evidence: str | dict | list,
                   claims: list[dict], chat_supported: dict[str, bool]) -> None:
    """Screen EVERY claim in ONE decision call and record a row per claim. Returns nothing:
    like `compare`, this changes no verdict.

    One call rather than one per claim because that is what these models are fastest and
    cheapest at — one evidence, many questions — and because it is the shape the cascade his
    answer asks for would use: *"a typed screen over each claim, with the prose judge called
    only on the claims it flags"*. Measuring the batch now means the eventual swap is measured
    in the form it will ship in, not in a convenient stand-in.
    """
    if not claims or not _available(ctx):
        return
    asked = [(f"c{i}", c) for i, c in enumerate(claims) if c.get("id")]
    if not asked:
        return
    probs: dict[str, float | None] = {}
    model = ""
    usage: dict = {}
    error = ""
    try:
        answered = _ask_many(
            ctx, [DecisionQuestion(name=name, type="yes_no",
                                   question=f"{question}\n\nTHE CLAIM: {c.get('text') or ''}")
                  for name, c in asked],
            evidence_text(evidence))
        model = answered.model or ""
        usage = answered.usage or {}
        probs = {a.name: a.probability for a in answered.answers}
    except Exception as exc:                      # every failure mode: see the module docstring
        error = str(exc)[:300]
        log.info("decision shadow at finish: %s", error)
    for name, claim in asked:
        prob = probs.get(name)
        verdict = None if prob is None else prob >= THRESHOLD
        chat = chat_supported.get(str(claim["id"]))
        row = {
            "ts": now_iso(),
            "site": "finish",
            "routine": getattr(getattr(ctx, "routine", None), "slug", ""),
            "run_id": getattr(ctx, "run_id", ""),
            "claim_id": str(claim["id"]),
            "chat_verdict": chat,
            "decision_verdict": verdict,
            "agree": None if verdict is None or chat is None else verdict == chat,
            "probability": prob,
            "threshold": THRESHOLD,
            "model": model,
            # the batch's usage rides the FIRST row only, so a fold that sums it reads the
            # call's real cost rather than multiplying it by the number of claims
            "usage": usage if name == asked[0][0] else {},
            "claims_in_call": len(asked),
        }
        if error:
            row["error"] = error
        elif verdict is None:
            row["error"] = "the answer carried no probability for this claim"
        _record(ctx, row)


def _record(ctx, row: dict) -> None:
    """Append one comparison, or give up silently — the run never learns of a write failure."""
    home = getattr(getattr(ctx, "server", None), "routines_home", None)
    if home is None:
        return
    try:
        append_jsonl(home / ".control" / SHADOW_FILE, row)
    except Exception as exc:                      # a measurement never raises
        log.info("decision shadow: could not record the comparison (%s)", exc)
