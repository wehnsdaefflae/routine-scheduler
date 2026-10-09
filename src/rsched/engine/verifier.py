"""Claim verification — a second look at what a finish says it MET.

The accounting makes a run answer for every line it owes (`accounting.py`). It cannot check
whether a `met` is TRUE: a run can write `d3 met: PDF verified` having never opened the PDF;
the record, the next run and the page would all agree it is done. The failure is silent and
confident, which is the bad kind.

So at the finish a SECOND model — the `tool_call` role, never the main one — is asked, per line
the accounting claims `met` (a Done-when line, or a finish-line outcome the run proves), whether
the run's own transcript supports the claim. A refuted claim sets the finish aside for one turn
and the model either does the work or restates its case.

## What the judge is shown, and why a stage read is only a HINT (F620)

The claim is checked against **what the run DID**: the judge reads the run's ACTION RECORD
(`loop.turn_records` — one line per turn, kind + brief + `say`, the record that survives
compaction) next to the tail of its conversation. The tail alone was not enough evidence to
judge a claim by: it is the last `TAIL_CHARS` characters of the message list, so on a long run
the actions that produced an early outcome are no longer in it, and the judge was asked to rule
on evidence it could not see.

A Done-when line may name the STAGE that produces it, and a run that never entered that stage
is a real signal — but a weak one, and it used to be a verdict all by itself: the objection was
raised with no model asked, and the blamed line was then EXCLUDED from the judge that would have
looked at the evidence. Two measurements say that is the wrong way round:

- entering a stage is detected from the run's own file activity (`engine/fileops.py` →
  `readmodels/statemap.stage_coverage`): a `read_file` on `stages/<name>.md`, or a
  `state/phase.json` the run wrote. Every recipe on this instance tells a run to read stage
  modules ON DEMAND, and a routine that keeps no phase cursor has only the module re-read left —
  so a run that legitimately did not re-read a module it already knew was told it never entered
  the stage. Measured on tv-show-tracker-seedbox-manager (R2228, run 20261003-160012): d4 and d5
  were produced in full — a drain verified at exact size, a `rutorrent-rpc erase`, a pointer
  advance — and all three stage-keyed lines were objected to with the same sentence.
- it is wrong in the other direction too: reading the module and producing nothing was credited.

So the stage signal is now carried to the judge as a NOTE ON THE CLAIM, and the judge rules on
the actions. The run keeps the last word exactly as before. An objection a run can overturn by
citing what it did is a check; one it can only concede to teaches every future run to concede.

## The two ways this could be worse than the problem

**False blocks.** A judge that blocks on doubt is a machine for stranding runs over evidence
that lives outside the tail it was shown. So the verifier is FAIL-OPEN at every level: it must
be confidently able to say the transcript contradicts or fails to show the claim before it
refutes, an unavailable endpoint or an unparseable answer accepts, and anything it does not
mention accepts. The default answer is always "the run's word stands".

**A livelock.** A stubborn model and a stubborn judge would trade refutations until the budget
dies. So a line is challenged AT MOST ONCE per run (`loop._challenged`). If the model re-asserts
the same verdict after being shown the objection, its verdict STANDS and the disagreement is
RECORDED — in the `stopping_update` event and on a finish-line outcome as `disputed`. The engine
gets one intervention, the model keeps the last word, and the operator gets the audit trail.
"Per run" is the guard scope (engine/guardscope.py) — per reply, in a conversation — and it
holds across a resume: each challenge is recorded on its deferral (`claims_unsupported`), and a
resumed leg rebuilds the set from those records (`finishgate.rebuild`).

Cost is naturally scoped: one subcall per finish attempt that claims something met.
"""

from __future__ import annotations

import logging

from ..schema_guard import SchemaViolation, extract_json

log = logging.getLogger("rsched.verifier")

#: How much of the run's own conversation the judge reads. The tail is where the evidence for a
#: just-claimed condition lives; the head is orientation and costs tokens to re-read.
TAIL_CHARS = 12_000
#: How much of the ACTION RECORD it reads (F620). One line per turn, so this is the cheaper and
#: the more complete of the two evidence sources: ~200 characters buys a whole turn here, where
#: the same spend on the conversation buys a fraction of one observation. Its MIDDLE is elided
#: before either end, because the evidence for a claimed outcome is as likely to sit at turn 12
#: as at turn 120 — which is exactly what the tail alone could not show.
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

    Read from `loop.turn_records`, which the loop appends as each action lands and a resume
    rebuilds (`compaction.turn_record`), so it SURVIVES compaction: this is the only evidence
    surface that still shows what a long run did at its beginning. The rendering is the
    compaction digest's own line builder, so the judge reads the turns in the same form the run
    itself reads its elided middle in.

    Over the cap, the MIDDLE goes and both ends stay, with a line saying how many turns were
    dropped — an outcome's evidence is as often early as late.
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
        f"ITS FINISH SUMMARY:\n{summary.strip()[:4000]}\n\n"
        + (f"WHAT IT DID — one line per turn, its own action record:\n{actions}\n\n"
           if actions else "")
        + f"ITS TRANSCRIPT (tail):\n{tail}")


def hinted(claims: list[dict], entered: set[str]) -> list[dict]:
    """The same claims, each carrying a `hint` where one applies — a note the judge reads under
    that claim (F620). Non-mutating; a claim with nothing to note comes back unchanged.

    The one hint today: the line's producing stage was never entered. That was a verdict of its
    own until F620, and the line it blamed was then excluded from the judge that would have read
    the evidence. Stage entry is detected from the run's own file activity (a `read_file` on
    `stages/<name>.md`, or a `state/phase.json` the run wrote), which measures module re-reads
    rather than work done — so it can be true of a run that produced the outcome in full. The
    wording is therefore aimed at a reader who will go and look at the actions.

    A hint informs the judgement; it never makes one.
    """
    return [{**c, "hint": (f"the run never entered the stage `{c['stage']}` that this recipe "
                           "says produces this outcome (no read of its module, no phase cursor "
                           "naming it) — which can also mean it knew the procedure already, so "
                           "judge it by the actions, not by this")}
            if c.get("stage") and c["stage"] not in entered else c
            for c in claims]


def _verdicts(completion) -> list | None:
    """The judge's verdict list, or None when its reply carries none.

    Read from the endpoint's native parse when it made one, else out of the reply TEXT — the
    shape every OpenAI-compatible adapter returns schema output in (`parsed` stays None there,
    and every other schema'd caller falls back to the text the same way). Reading `parsed`
    alone made the check a silent no-op whenever the `tool_call` role was served by one of
    them: every claim was accepted unread.
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

    A claim may carry a `hint` — a weak signal that is not a verdict of its own (`hinted`); the
    judge reads it under that claim. It informs the judgement; it never makes one.
    """
    if not claims:
        return []
    ctx = loop.ctx
    try:
        endpoint, ref = ctx.registry.for_model("tool_call", ctx.routine.models)
        completion = endpoint.complete(
            [{"role": "user", "content": _prompt(claims, summary, _tail(loop),
                                                 _actions(loop))}],
            model=ref.model, schema=VERDICT_SCHEMA, effort=ref.effort,
            temperature=ref.temperature, max_tokens=ref.max_tokens,
            purpose="finish · verify claims", kind="llm_action")
    except Exception as exc:
        # fail-open, loudly: whatever went wrong — no model for the role, a provider error, an
        # adapter bug — the run's word stands, at its finish of all moments, and the operator
        # can see why it was not checked
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

    It says what would overturn the objection, in the terms the judge decides on: the ACTIONS
    that produced the outcome and the identifiers they carried. A run that had the evidence and
    read this as unanswerable filed a false `unmet` about its own finished work (F620/R2228) —
    and a false `unmet` is worse than a missing one, because the accounting is where the
    operator reads whether a run did what it claimed.
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
