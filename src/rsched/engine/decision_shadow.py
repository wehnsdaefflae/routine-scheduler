"""A decision model answers the SAME typed question as the chat model, and both are recorded.

Step 1 of D287 (reroute the refusal classifier, cascade the finish verifier), and it REROUTES
NOTHING — deliberately. The chat model keeps deciding (`engine/refusal.py`: only an LLM
classification subcall decides the non-obvious cases); the decision model answers beside it and
the pair goes into a stream, so the swap can be decided on measured agreement rather than price.
Measuring first matters because a typed model's argmax can stay stable while its probability
mass moves under it — a reader of verdicts alone would see consistency that is not there.

The stream is durable and instance-wide (`.control/decision-shadow.jsonl`): run dirs fall to
retention and agreement is a question over hundreds of calls. Each row carries both verdicts,
the probability (the argmax is what hides instability) and the threshold (so a reader can
rescore).

FAILURE DISCIPLINE: a measurement that can fail a run is worse than none. Every failure mode —
no decision model, a provider error, a malformed answer, an unwritable stream — returns the chat
verdict untouched. An unanswered comparison records `decision_verdict: null` and `agree: null`,
never `false`: an absent verdict is not a negative one.
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

#: The two sites D287 names: `refusal · classify reply` and `finish · verify claims`.
SITES = ("refusal", "finish")

#: P(yes) at or above this reads as a yes. 0.5 is the only threshold that is not a tuning
#: choice, and it is recorded in every row so a later reader can rescore the whole stream.
THRESHOLD = 0.5

_TIMEOUT_S = 20


def _ask_many(ctx, questions: list[DecisionQuestion], evidence: str):
    """Put SEVERAL typed questions over one evidence to this routine's decision model — the
    transport boundary of this module, one seam for both forms. Raises on any transport
    failure; `compare` and `compare_claims` are the ones that may not.
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

    The return value is the contract: this function is an observer, so callers may wrap their
    existing verdict in it without changing what the run does.
    """
    if not _available(ctx):
        # nothing to compare, nothing to record — a "not configured" row per call is noise
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


def screen_claims(ctx, *, question: str, evidence: str | dict | list,
                  claims: list[dict]) -> dict[str, float | None]:
    """The screen's probability per claim id, BEFORE the prose judge runs — `{}` when there is
    no decision model, and `None` for any claim the answer did not cover.

    This is the half D302 cascades on. It makes no verdict and records nothing: the recording
    is `compare_claims`'s, after the prose judge has (or has not) spoken, so one stream row
    still carries both verdicts and the window stays comparable with the measurement the
    table was built from.
    """
    if not claims or not _available(ctx):
        return {}
    asked = [(f"c{i}", c) for i, c in enumerate(claims) if c.get("id")]
    if not asked:
        return {}
    try:
        answered = _ask_many(
            ctx, [DecisionQuestion(name=name, type="yes_no",
                                   question=f"{question}\n\nTHE CLAIM: {c.get('text') or ''}")
                  for name, c in asked],
            evidence_text(evidence))
    except Exception as exc:                      # every failure mode: see the module docstring
        log.info("claim screen: %s", str(exc)[:300])
        return {}
    probs = {a.name: a.probability for a in answered.answers}
    return {str(c["id"]): probs.get(name) for name, c in asked}


def compare_claims(ctx, *, question: str, evidence: str | dict | list,
                   claims: list[dict], chat_supported: dict[str, bool],
                   screen: dict[str, float | None] | None = None,
                   judge_skipped: bool = False) -> None:
    """Screen EVERY claim in ONE decision call and record a row per claim. Returns nothing:
    like `compare`, this changes no verdict.

    One call rather than one per claim: one evidence, many questions is what these models are
    cheapest at, and it is the shape the cascade ships in. `screen` carries probabilities the
    caller ALREADY obtained (`screen_claims`), so a cascaded finish asks the decision model
    once rather than twice — without it the measurement is made here, exactly as before.
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
    if screen is not None:
        # the cascade already asked: record ITS answers rather than paying for a second call
        probs = {name: screen.get(str(c["id"])) for name, c in asked}
    else:
        try:
            answered = _ask_many(
                ctx, [DecisionQuestion(name=name, type="yes_no",
                                       question=f"{question}\n\nTHE CLAIM: "
                                                f"{c.get('text') or ''}")
                      for name, c in asked],
                evidence_text(evidence))
            model = answered.model or ""
            usage = answered.usage or {}
            probs = {a.name: a.probability for a in answered.answers}
        except Exception as exc:                  # every failure mode: see the module docstring
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
            # D302: TRUE means the cascade answered for the prose judge, so `chat_verdict` is
            # null because no judge ran — not because one ran and said nothing. Without this
            # the two are indistinguishable in the stream and every later agreement figure
            # would silently include claims nothing was compared against.
            "judge_skipped": judge_skipped,
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
