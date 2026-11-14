"""The self-audit LEDGER as question items — the Decisions page's third source.

Split out of `web/decisions_read.py` (R3417), which crossed the house size bar when the proof
reader landed. The boundary was already there: this file answers one question — which of
`self-audit`'s own decision rows are OPEN asks, and what each one carries — while
`decisions_read` gathers the three sources and derives the answered state common to all of them.

The subject is not symmetrical with the other two sources, which is why it earns a module: a
ledger row is another routine's FILE, with its own status vocabulary, its own answered-marker
records, and (since R3417) its own per-option proof that has to be validated rather than
trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import read_json
from ..readmodels import decision_answers, memo, stamps

_DECISION_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\]")

#: Statuses that mean the decision is already MADE — the report carries them so the Items
#: page can show progress, but they are not asks. `in_progress` is the big one: self-audit
#: is already building the thing, so queueing it for an answer is a card with nothing to
#: choose (2026-08-06: D74/D76/D77/D78/D80 all reached the operator this way in one night).
#: `addressed` and `dropped` complete `docs/items.md`'s disposal vocabulary, and their
#: absence re-asked every decision self-audit had SHIPPED: six already-addressed decisions
#: (D167-D173) came back as answerable cards on 2026-10-10 and cost ~19 minutes of
#: re-answering, one of them returning the OPPOSITE option to the one in the code (F659).
#: The `marker >= generated` gate below cannot catch that — an answer's marker is always
#: older than the report written after it.
_DECIDED_STATUSES = frozenset({"settled", "closed", "done", "in_progress", "in progress",
                               "authorized", "shipped", "building", "addressed", "dropped"})



def _audit_decisions(server) -> list[dict]:
    """The self-audit report's open decisions (`_audit_decisions_fresh`), reused while the
    report, the durable answered-markers and the inbox (the queued answers) are unchanged.
    """
    from ..priorities import SELF_AUDIT_SLUG

    rdir = server.routines_home / SELF_AUDIT_SLUG
    return memo.memoized(f"decisions:audit:{rdir}",
                         [rdir / "audit" / "report.json",
                          rdir / "audit" / "decisions-answered.json", rdir / "inbox"],
                         lambda: _audit_decisions_fresh(rdir))


def _ledger_proof(decision: dict, options: list[str]) -> dict:
    """One ledger decision's per-option PROOF, lifted onto its question item — or `{}`.

    R3417, the step between the two routines. `self-audit`'s ledger writer now refuses to file
    a decision without a proof per option (`proofs.REQUIRED`, `proofs.CATEGORIES` and the five
    states, deliberately mirrored from this engine's own module so the two cannot disagree) —
    but nothing READ it: `_audit_decisions_fresh` built its question items from id, options,
    status and detail alone, so a proof on a ledger row reached no surface at all. The Unproven
    band (F795, 0.520.0) therefore covered engine-filed asks and not ledger decisions, which
    are the ones that most often sit answered-and-unbuilt.

    VALIDATED THROUGH `proofs.problems`, not trusted: the ledger is another routine's file, so
    a malformed map is possible, and a proof the engine cannot read is worse than none — it
    would put a row in the Unproven band whose state no predicate could ever clear. A map that
    does not validate is dropped and the row renders exactly as it did before.

    What it deliberately does NOT do is INVENT one. The 157 pre-existing rows carry no proof,
    and `proofs.unproven` is False for a record without one — which is the property that keeps
    this from re-opening the instance's whole settled history (the F659 regression D220's
    closure warned about). The proofless set shrinks as `self-audit` touches rows, not by a
    back-fill here.
    """
    from .. import proofs

    spec = decision.get("proof")
    if not isinstance(spec, dict) or not spec or proofs.problems(spec, options):
        return {}
    return {str(option): dict(entry) for option, entry in spec.items()
            if isinstance(entry, dict)}


def _audit_decisions_fresh(rdir: Path) -> list[dict]:
    """The self-audit report's OPEN decisions as meta-badged question items. A decision
    leaves the inbox when an answer is queued for it, when the report marks it decided
    (`_DECIDED_STATUSES` — or the routine's prose convention, a detail starting with
    SETTLED), or when it offers EXACTLY ONE option.

    The one-option rule is the load-bearing half: an already-decided item re-presented as
    a card whose only option restates the decision ("phase 1 next run") costs the operator
    a read and a click for no choice, and it is the shape self-audit reaches for whenever
    it wants an acknowledgment. Zero options stays open on purpose — that is a free-text
    ask, which the answer POST accepts. Nothing is hidden by this: every decision, at every
    status, is still listed on the Messages page (`readmodels.items`) with its status.
    """
    from ..priorities import SELF_AUDIT_SLUG
    from .api_audit import queued_messages

    report = read_json(rdir / "audit" / "report.json")
    if not isinstance(report, dict):
        return []
    queued = {m.group(1).strip() for p in queued_messages(rdir)
              if (m := _DECISION_RE.match(p.get("text") or ""))}
    # Durable answered markers: a mid-run delivery consumes the inbox message instantly,
    # so `queued` alone cannot keep an answered decision out of the inbox while the report
    # still lists it open — the user would be asked the same decision again and again.
    # A decision answered at-or-after this report's `generated` stays hidden until a NEWER
    # report explicitly lists it open again.
    answered = read_json(rdir / "audit" / "decisions-answered.json")
    if not isinstance(answered, dict):
        answered = {}
    words = decision_answers.queued_answer_words(rdir)
    generated = str(report.get("generated") or "")
    out = []
    for d in report.get("decisions") or []:
        did = str(d.get("id") or "").strip()
        options = [str(o) for o in (d.get("options") or [])]
        proof = _ledger_proof(d, options)
        status = str(d.get("status") or "").strip().lower()
        # `in_progress` HIDES a row — except one that carries a proof (R3417, the second half).
        # D220's closure named the sequencing and stopped one step short of it: dropping
        # `in_progress` from `_DECIDED_STATUSES` outright would hide nothing and un-hide 157
        # legacy rows that nothing can ever clear, which is the F659 regression (six
        # already-addressed decisions came back as answerable cards and cost ~19 minutes, one
        # answered the OPPOSITE way). A row with a VALID per-option proof is the one case where
        # "already building it" is a claim the page can check instead of trust: its state says
        # whether the work landed, so it belongs in the Unproven band until some option reads
        # positive. Measured on the live ledger the day this shipped: 5 rows carry a proof and
        # 4 of them are `in_progress` — D287, D288, D289, D291, every one answered by the
        # operator and none of them built, which is exactly the set this band exists for.
        hidden_by_status = status in _DECIDED_STATUSES and not (proof and status == "in_progress")
        settled = (hidden_by_status
                   or str(d.get("detail") or "").lstrip().upper().startswith("SETTLED"))
        if not did or settled or len(options) == 1:
            continue
        # ANSWERED, by the two records his click writes (`web/api_audit.write_feedback`). The
        # durable marker decides when it exists, because it is the one with a timestamp: an
        # answer at-or-after this report is answered, and a NEWER report re-asking the
        # decision outranks it (self-audit re-asks deliberately). A queued message with no
        # marker has no timestamp to compare, so it reads as answered, as it always did.
        marker = str(answered.get(did) or "")
        # as INSTANTS, never as text (F771): both stamps are host-local with their offset, and
        # text ordering reverses across a DST change — on 2026-10-25 six answers 27-38 minutes
        # NEWER than the report all sorted below it, so every one was re-asked.
        was_answered = stamps.at_or_after(marker, generated) if marker else did in queued
        # A PROOF-CARRYING `in_progress` row is answered by the ledger's own record of it, and
        # the marker gate cannot see that: an answer recorded in the report is OLDER than the
        # report that records it, so `at_or_after` reads False and the row would render as an
        # unanswered card — the opposite of the truth, and `proofs.unproven` needs `answered`
        # to be true to put it in the band at all. `resolution` is written only by self-audit
        # and only from his click (`decisions-answered.json` + the inbox message are its
        # sources), so it is his answer, recorded. Measured on the live ledger: without this,
        # D287/D288/D289/D291 each read `answered: False, unproven: False` while carrying an
        # `in_progress` proof — i.e. the band stayed empty and the whole reader bought nothing.
        resolution = str(d.get("resolution") or "").strip()
        if proof and status == "in_progress" and resolution:
            was_answered = True
        text = str(d.get("title") or did)
        if d.get("detail"):
            text += "\n\n" + str(d["detail"])
        row = {"qid": f"audit:{did}", "routine": SELF_AUDIT_SLUG, "mode": "deferred",
               "meta": True, "question": text, "options": options,
               "asked": generated}
        if proof:
            row["proof"] = proof
        if was_answered:
            # F529, the acknowledgement half: this row used to be DROPPED, so the card he had
            # just answered vanished and nothing anywhere said his answer had landed — "i
            # answered your questions. for the second time btw. did my answers get lost the
            # first time?!". It is not a new ask: every surface that counts what waits filters
            # `answered` out (views/questions.js, web/push.pending), and the page renders it in
            # the Settled band with his own words, like every other decision shape (F525).
            row["answered"] = True
            # His own words first (the queued inbox message), then the ledger's record of them
            # (`resolution` — what self-audit wrote down from his click, and the only thing left
            # once a run has consumed the message), then the bare marker. Without the middle
            # one a proof-carrying `in_progress` row rendered "answered " with nothing after it,
            # because its marker predates the report and is not read here.
            row["answer"] = words.get(did.upper()) or resolution or f"answered {marker}"
            row["answer_source"] = "web"
            # `settled` = a run has taken it: the message is gone from the inbox, so the
            # marker is all that is left (the page says "read by the run that asked").
            row["settled"] = did not in queued
        out.append(row)
    return out

