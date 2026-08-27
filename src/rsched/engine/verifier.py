"""Stopping-condition verification — v2 of F334/D98.

v1 makes a run ACCOUNT for its goal: the finish gate confirms the summary carries a
`[s<n>] met|unmet` line per active condition. It cannot check whether the line is TRUE. A run
can write `[s3] met — PDF verified` having never opened the PDF, and v1, the writer and the
panel will all agree it is done. The failure is silent and confident, which is the bad kind.

v2 closes that: at the finish, a SECOND model — the `tool_call` role, never the main one — is
asked, per condition the summary claims `met`, whether the run's own transcript supports the
claim. A refuted claim sets the finish aside for one turn, the way an unaccounted one already
does, and the model either does the work or restates its case.

## The two ways this could be worse than the problem

**False blocks.** A judge that blocks on doubt is a machine for stranding runs over evidence
that lives outside the tail it was shown. So the verifier is FAIL-OPEN at every level: it must
be confidently able to say the transcript contradicts or fails to show the claim before it
refutes, an unavailable endpoint or an unparseable answer accepts, and anything it does not
mention accepts. The default answer is always "the run's word stands".

**A livelock.** A stubborn model and a stubborn judge would trade refutations until the budget
dies — and the budget dying is exactly the outcome stopping conditions exist to replace. So a
condition is challenged AT MOST ONCE per run (`loop._challenged`). If the model re-asserts the
same verdict after being shown the objection, its verdict STANDS and the disagreement is
RECORDED — on the condition (`disputed`), in the `stopping_update` event, and in the panel. The
engine gets one intervention, the model keeps the last word, and the operator gets the audit
trail. An enforcement that can hang a run is not enforcement, it is a new failure mode.

Cost is naturally scoped: one subcall per finish attempt, and only for a run that HAS active
conditions claiming `met`. A run with no goal pays nothing, which is most runs.
"""

from __future__ import annotations

import logging

from ..endpoints.base import EndpointError
from . import stopping

log = logging.getLogger("rsched.verifier")

#: How much of the run's own conversation the judge reads. The tail is where the evidence for a
#: just-claimed condition lives; the head is orientation and costs tokens to re-read.
TAIL_CHARS = 12_000
CONDITION_CAP = 400

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {
        "type": "array",
        "description": "one entry per condition you were given, in the same order",
        "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "supported", "evidence"],
            "properties": {
                "id": {"type": "string", "description": "the condition id, e.g. s1"},
                "supported": {
                    "type": "boolean",
                    "description": "true = the transcript SHOWS the run doing what the claim "
                                   "says, or you are not certain it does not; false ONLY when "
                                   "the transcript positively contradicts the claim or shows "
                                   "the work was never attempted. When in doubt answer true"},
                "evidence": {
                    "type": "string",
                    "description": "the specific action or observation you relied on; when "
                                   "supported is false, what is missing or contradicts it"},
            }}}},
}


def _tail(loop) -> str:
    """The run's own recent conversation, oldest-last-N-chars, system message excluded."""
    parts = [str(m.get("content") or "") for m in loop.messages if m.get("role") != "system"]
    return "\n\n".join(parts)[-TAIL_CHARS:]


def _prompt(claims: list[dict], summary: str, tail: str) -> str:
    lines = "\n".join(f"- [{c['id']}] {c['text'][:CONDITION_CAP]}" for c in claims)
    return (
        "Another agent has just finished a job and claims it MET the stopping conditions "
        "below. Your ONLY task is to check each claim against the agent's own transcript.\n\n"
        "Answer `supported: true` when the transcript shows the agent doing what the claim "
        "says — or when you simply cannot tell. Answer `supported: false` ONLY when the "
        "transcript positively contradicts the claim, or shows the work was never attempted "
        "at all. You are looking at a TAIL of the transcript, so absence of evidence is NOT "
        "evidence of absence: if the work could plausibly have happened earlier, answer true. "
        "A wrong `false` strands a finished job; a wrong `true` costs nothing but a stale "
        "mark a human can correct. Be generous.\n\n"
        f"CONDITIONS THE AGENT CLAIMS ARE MET:\n{lines}\n\n"
        f"ITS FINISH SUMMARY:\n{summary.strip()[:4000]}\n\n"
        f"ITS TRANSCRIPT (tail):\n{tail}")


def refuted(loop, summary: str) -> list[dict]:
    """The conditions this summary claims `met` that the run's transcript does not support.

    Returns `[{id, text, evidence}]` — empty when there is nothing to check, when every claim
    stands, or when the check could not run at all. Never raises: a verifier that can break a
    run is worse than no verifier.
    """
    ctx = loop.ctx
    doc = stopping.load(ctx.routine.dir)
    active = {c["id"]: c for c in stopping.active(doc, phase=ctx.phase)}
    claims = [active[cid] for cid, (state, _note) in stopping.read_accounting(summary).items()
              if state == "met" and cid in active]
    if not claims:
        return []
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        completion = endpoint.complete(
            [{"role": "user", "content": _prompt(claims, summary, _tail(loop))}],
            model=ref.model, schema=VERDICT_SCHEMA, effort=ref.effort,
            temperature=ref.temperature, max_tokens=ref.max_tokens,
            purpose="stopping · verify claims", kind="llm_action")
    except (EndpointError, AttributeError, ValueError) as exc:
        # fail-open, loudly: the run's word stands and the operator can see why it was not checked
        log.warning("stopping: could not verify the finish claims (%s) — accepting them", exc)
        return []
    ctx.add_usage(completion.usage)
    parsed = completion.parsed
    if not isinstance(parsed, dict):
        return []
    by_id = {c["id"]: c for c in claims}
    out = []
    for v in parsed.get("verdicts") or []:
        # only an explicit, well-formed refutation of a condition actually claimed counts
        if not isinstance(v, dict) or v.get("supported") is not False:
            continue
        cond = by_id.get(str(v.get("id") or ""))
        if cond is not None:
            out.append({"id": cond["id"], "text": cond["text"],
                        "evidence": str(v.get("evidence") or "").strip()
                        or "the transcript does not show this being done"})
    return out


def challenge_message(items: list[dict]) -> str:
    """What the run is told when a claim is refuted — the objection and what to do with it."""
    lines = "\n".join(f"- [{i['id']}] {i['text']}\n  objection: {i['evidence']}" for i in items)
    return ("OBSERVATION (finish deferred): a check of your own transcript does not support "
            "every stopping condition you marked met.\n" + lines
            + "\n\nEither do the missing work and finish again, or — if the check is wrong, "
              "which it can be, since it reads only the tail of your transcript — finish again "
              "with the SAME verdict and point at the evidence. You will not be asked twice: "
              "a repeated verdict stands, and the disagreement is recorded for the user.")
