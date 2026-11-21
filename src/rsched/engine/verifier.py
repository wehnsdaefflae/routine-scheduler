"""Claim verification — a second look at what a finish says it MET.

The accounting makes a run answer for every line it owes (`accounting.py`) but cannot check
whether a `met` is TRUE. So at the finish a second CALL on the `tool_call` role — a fresh
context, not the run's own — is asked, per line claimed `met` (a Done-when line, or a
finish-line outcome the run proves), whether the run's record supports it. A refuted claim
sets the finish aside for one turn; the model either does the work or restates its case. It is
a second MODEL only when that role resolves to a different one; `rsched validate` names a
routine whose verifier shares its main model (`rsched/verifier_model.py`).

The judge reads the run's ACTION RECORD (`loop.turn_records`, one line per turn, which survives
compaction) next to the tail of its conversation — the tail alone misses early actions on a
long run. A Done-when line's producing STAGE never entered is only a HINT on the claim, never a
verdict (F620): stage entry is detected from module re-reads, not work done.

Guards against being worse than the problem:

- FAIL-OPEN at every level: refute only when confident the record contradicts the claim; an
  unavailable endpoint, an unparseable answer or an unmentioned claim accepts.
- No livelock: a line is challenged AT MOST ONCE per guard scope (`loop._challenged`,
  engine/guardscope.py). A re-asserted verdict STANDS and is recorded as `disputed`; a resumed
  leg rebuilds the set from the deferrals' `claims_unsupported` (`finishgate.rebuild`).

Cost: one subcall per finish attempt that claims something met.
"""

from __future__ import annotations

import logging

from ..schema_guard import SchemaViolation, extract_json

log = logging.getLogger("rsched.verifier")

#: How much of the run's own conversation the judge reads (the tail; the head is orientation).
TAIL_CHARS = 12_000
#: How much of the ACTION RECORD it reads — the cheaper, more complete evidence source. Its
#: MIDDLE is elided first: an outcome's evidence is as likely early as late.
ACTIONS_CHARS = 24_000
CONDITION_CAP = 400

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {
        "type": "array",
        "description": "one entry per claim you were given, in the same order",
        "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "supported", "evidence"],
            "properties": {
                "id": {"type": "string", "description": "the claim's id, e.g. d1 or g1"},
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


def _actions(loop) -> str:
    """Every action this run took, one line per turn — `turn <n>: <kind> <brief> — say: "…"`.

    Read from `loop.turn_records` (rebuilt on resume, survives compaction), rendered by the
    compaction digest's own line builder. Over the cap the MIDDLE goes, with a line counting
    the dropped turns.
    """
    from .compaction import _digest_line

    records = getattr(loop, "turn_records", None) or []
    lines = []
    for r in records:
        try:
            lines.append(_digest_line(r))
        except (KeyError, TypeError):
            continue        # a malformed record is not evidence; it must not break the judge
    if not lines:
        return ""
    text = "\n".join(lines)
    if len(text) <= ACTIONS_CHARS:
        return text
    half = ACTIONS_CHARS // 2
    head, tail = text[:half], text[-half:]
    kept = head.count("\n") + tail.count("\n") + 1
    return (f"{head}\n… {max(0, len(lines) - kept)} further turns elided from the middle "
            f"of this record …\n{tail}")


def _prompt(claims: list[dict], summary: str, tail: str, actions: str = "") -> str:
    lines = "\n".join(
        f"- [{c['id']}] {c['text'][:CONDITION_CAP]}"
        + (f"\n  note: {c['hint']}" if c.get("hint") else "")
        for c in claims)
    return (
        "Another agent has just finished a job and claims it MET the outcomes below. Your ONLY "
        "task is to check each claim against what the agent actually DID.\n\n"
        "Judge by the ACTIONS and their results — a file written, a command run and what it "
        "returned, a message sent, a record advanced — matching the identifiers the claim is "
        "about. A claim is supported when the actions amount to the outcome, whatever words "
        "the agent used for it; a claim is NOT refuted merely because some step you would have "
        "taken is absent, or because a procedure document went unread.\n\n"
        "Answer `supported: true` when the record shows the agent doing what the claim says — "
        "or when you simply cannot tell. Answer `supported: false` ONLY when the record "
        "positively contradicts the claim, or shows the work was never attempted at all. The "
        "conversation below is only its TAIL and the action record may have its middle elided, "
        "so absence of evidence is NOT evidence of absence: if the work could plausibly have "
        "happened in what you cannot see, answer true. A wrong `false` strands a finished job "
        "and teaches the agent that conceding is cheaper than being right; a wrong `true` "
        "costs nothing but a stale mark a human can correct. Be generous.\n\n"
        f"OUTCOMES THE AGENT CLAIMS ARE MET:\n{lines}\n\n"
        "ITS FINISH SUMMARY — a two-part summary by contract, `**Process:**` (how the run "
        "went) then `**Work:**` (the task: the outcome with its numbers, its conditions and "
        "consequences). A claim about what the summary SAYS is met wherever the summary says "
        f"it, and the outcome's place is the `**Work:**` part:\n{summary.strip()[:4000]}\n\n"
        + (f"WHAT IT DID — one line per turn, its own action record:\n{actions}\n\n"
           if actions else "")
        + f"ITS TRANSCRIPT (tail):\n{tail}")


def hinted(claims: list[dict], entered: set[str]) -> list[dict]:
    """The same claims, each carrying a `hint` where one applies — a note the judge reads under
    that claim. Non-mutating.

    The one hint: the line's producing stage was never entered (no read of `stages/<name>.md`,
    no `state/phase.json` naming it). That measures module re-reads, not work done, so a hint
    informs the judgement and never makes one.
    """
    return [{**c, "hint": (f"the run never entered the stage `{c['stage']}` that this recipe "
                           "says produces this outcome (no read of its module, no phase cursor "
                           "naming it) — which can also mean it knew the procedure already, so "
                           "judge it by the actions, not by this")}
            if c.get("stage") and c["stage"] not in entered else c
            for c in claims]


def _verdicts(completion) -> list | None:
    """The judge's verdict list, or None when its reply carries none.

    Read from the endpoint's native parse, else out of the reply TEXT — OpenAI-compatible
    adapters leave `parsed` None, and reading `parsed` alone would accept every claim unread.
    """
    parsed = completion.parsed
    if parsed is None:
        try:
            parsed = extract_json(completion.text or "")
        except SchemaViolation:
            return None
    verdicts = parsed.get("verdicts") if isinstance(parsed, dict) else None
    return verdicts if isinstance(verdicts, list) else None


def refuted(loop, claims: list[dict], summary: str) -> list[dict]:
    """The claims (`[{id, text}]`) the run's own record does not support, as `[{id, text,
    evidence}]` — empty when there is nothing to check, when every claim stands, or when the
    check could not run at all. Never raises: a verifier that can break a run is worse than no
    verifier.

    A claim may carry a `hint` (`hinted`), read by the judge under that claim.
    """
    if not claims:
        return []
    ctx = loop.ctx
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models, ctx.routine.efforts)
        completion = endpoint.complete(
            [{"role": "user", "content": _prompt(claims, summary, _tail(loop),
                                                 _actions(loop))}],
            model=ref.model, schema=VERDICT_SCHEMA, effort=ref.effort,
            temperature=ref.temperature, max_tokens=ref.max_tokens,
            purpose="finish · verify claims", kind="llm_action")
    except Exception as exc:
        # fail-open, loudly: whatever went wrong, the run's word stands and the log says why
        log.warning("finish: could not verify the met claims (%s) — accepting them", exc)
        return []
    ctx.add_usage(completion.usage)
    verdicts = _verdicts(completion)
    if verdicts is None:
        log.warning("finish: the claim judge answered without a verdict list — accepting the "
                    "met claims unchecked")
        return []
    by_id = {c["id"]: c for c in claims}
    out = []
    for v in verdicts:
        # only an explicit, well-formed refutation of a line actually claimed counts
        if not isinstance(v, dict) or v.get("supported") is not False:
            continue
        claim = by_id.get(str(v.get("id") or ""))
        if claim is not None:
            out.append({"id": claim["id"], "text": claim["text"],
                        "evidence": str(v.get("evidence") or "").strip()
                        or "the transcript does not show this being done"})
    return out


def challenge_message(items: list[dict]) -> str:
    """What the run is told when a claim is refuted — the objection and how to overturn it.

    It names what overturns the objection in the judge's own terms — the ACTIONS and their
    identifiers — so a run with the evidence cites it instead of conceding a false `unmet`.
    """
    lines = "\n".join(f"- [{i['id']}] {i['text']}\n  objection: {i['evidence']}" for i in items)
    return ("OBSERVATION (finish deferred): a check of your own record does not support "
            "every line your accounting marks met.\n" + lines
            + "\n\nEither do the missing work and finish again, or — if the check is wrong, "
              "which it can be, since it reads only a tail of your conversation and a "
              "one-line-per-turn record — finish again with the SAME verdict and CITE THE "
              "ACTIONS: which turns produced the outcome, what they returned, and the "
              "identifiers that match (a file written, a command's result, a record "
              "advanced). Pointing at your own actions is how this verdict is overturned, and "
              "it is the right answer whenever you have them: do not concede a line you did "
              "the work for. You will not be asked twice — a repeated verdict stands, and the "
              "disagreement is recorded for the user.")
