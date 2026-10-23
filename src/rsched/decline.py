"""The Decisions page's first-class **no** — what a declined decision says, and what the
asking RUN is told about it (R2197).

A decision could be answered, snoozed or deferred, and none of those is a refusal: an answer
closes the item only by *saying something*, a snooze is UI noise control that leaves the
question open in every run's digest, and a defer releases a parked run onto its stated default
with the record still open. So an operator who had read a proposal and did not want it could
only type prose, snooze in circles, or leave it open forever — and the routine that asked had
to parse a sentence to find out, if it could tell at all.

Declining writes the ORDINARY answer payload (`inbox/answer-<qid>.json`), so the item leaves
the open set exactly as an answer does (`web/decisions_read._mark_answered`) and
`collect_deferred_answers` drains it unchanged — no second consumption path. What the decline
adds is a VALUE: `decision: "declined"` on the record, carried onto the answer pair both
delivery seams read, so the asking run matches a token instead of reading prose.

WHY A QUESTION-SCOPED VALUE AND NOT `engine/requests.DECISIONS`. That vocabulary is the one
structured precedent in the tree (allow/deny × now/forever, where denial is first-class) and
reusing it was the design point left open. It is GRANT-scoped in both its meaning and its
mechanics: each value decides an ENTITY (`deny_now` = "declined for this run — work without it
and do not re-request it now"), the engine READS those values to seed a run's grant overlay
(`requests.apply_deferred_decisions` → `_seed`), and `web/api_questions._decide_request`
refuses a decision on a question that carries no `request` ids at all (400, "this question is
not an access request — answer it with text"). Putting a grant word on a proposal would
therefore mean loosening exactly the validation that keeps the two apart, and would leave the
overlay bridge inert only by the accident of its two guards. A proposal declined for good is
also not "declined for this run". So: one value here, one phrase, and this module is the single
place a later unification would merge the two — which the authorized increment explicitly
leaves for after the verb exists and is drained correctly.

Lives at the top level, not under `web/`, for `patchreceipt.py`'s reason: the web layer WRITES
these fields and the engine READS them (`engine/inbox_questions`, `engine/composer`,
`engine/control`), and `engine/` imports nothing from `web/`.
"""

from __future__ import annotations

from .ids import now_iso

#: The answer-file / API-row value. A token, because the point of the whole module is that
#: nobody has to read a sentence to learn that the answer was no.
DECISION = "declined"

#: The answer `text`, which every surface renders — the Decisions page's settled receipt, the
#: state digest's `A:` line, a channel note. Exactly how `requests.DECISION_PHRASES` makes the
#: text carry the decision's meaning so one wording reaches all of them.
PHRASE = ("declined — the operator read this and said no. It is settled: do not re-propose it "
          "unchanged, and do not treat the absence of an answer as the obstacle")


def payload(qid: str, reason: str = "") -> dict:
    """The answer-file payload for a declined decision — the SAME shape an answer writes.

    `text` is the phrase (plus the operator's reason when he gave one), so every reader that
    only renders text already says the right thing; `decision` is the value, for every reader
    that should not have to parse it.
    """
    note = str(reason or "").strip()
    return {"qid": qid, "text": f"{PHRASE}. Reason: {note}" if note else f"{PHRASE}.",
            "decision": DECISION, "reason": note, "intermediate": False,
            "source": "web", "ts": now_iso()}


def declined(obj: dict) -> bool:
    """True when this answer file / answer pair / API row is a decline."""
    return isinstance(obj, dict) and str(obj.get("decision") or "") == DECISION


def carry(pair: dict, answer: dict) -> dict:
    """Copy the decline verdict from an answer FILE onto the answer pair a run consumes.

    Exactly how `request`/`decision` and the patch receipt already ride a pair
    (`engine/inbox_questions`): the pair is what both delivery seams read — the boot digest and
    the live turn boundary — so a fact missing here reaches no run at all. Absent when the
    answer is not a decline, so an ordinary answer gains nothing and no reader learns to expect
    it.
    """
    if declined(answer):
        pair["decision"] = DECISION
        if str(answer.get("reason") or "").strip():
            pair["reason"] = str(answer["reason"]).strip()
    return pair


def digest_line(pair: dict) -> str:
    """The line the asking RUN reads under its answer, or "" — the point of the whole module.

    Written to be impossible to mistake for its opposite: this is read mid-run by a model
    deciding what to do next, and the failure it replaces is a model reading a polite refusal
    as a go-ahead. It names the value, says the item is closed, and says what NOT to do.
    """
    if not declined(pair):
        return ""
    why = str(pair.get("reason") or "").strip()
    return (f"decision: {DECISION} — this proposal was REFUSED, not merely left unanswered. "
            f"Drop it: do not re-propose it unchanged and do not build on it."
            + (f" The operator's reason: {why}" if why else ""))


def digest_suffix(pair: dict) -> str:
    """`digest_line` as an indented continuation line, or "" — for a renderer appending it
    under an existing `A:` line (the state digest, the mid-run answer injection).
    """
    line = digest_line(pair)
    return f"\n  {line}" if line else ""
