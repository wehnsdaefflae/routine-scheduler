"""What an ANSWER says about the `config_patch` its question carried — and what the asking
RUN is told about it (R2189 built the first half; R2778 is this one).

A patch-carrying decision card has TWO independent controls: the Decisions page's
`approve & apply` button, which PATCHes the target and verifies every key landed, and the
ordinary answer, which lands text and applies nothing (the operator's ruling, 2026-10-05:
*only the button applies; answering warns and keeps the patch available*). R2189 made the
answer SAY which happened — but only to the person who had just clicked. The run that asked
learned nothing: `collect_deferred_answers` dropped the fact and the digest rendered
`- Q: … / A: …`. So a routine that asked for a 600-turn budget, was answered "Ok", recorded
the raise as applied and planned against a ceiling that was still 400 — `budget_exhausted`
on 2026-10-19 and again on 2026-10-20, both `limit=400`.

**The flag has to be true in BOTH directions, and that is the whole difficulty.** The apply
button PATCHes first and THEN posts an ordinary answer, so an answer filed for a patch that
really did apply used to carry `config_patch_applied: False` too. A run told "NOT APPLIED"
off that flag alone would be told a falsehood on the one path that works — strictly worse
than the old silence. The answer POST therefore carries an explicit applied signal that the
button sets (`web.api_questions.Answer.config_patch_applied`), and `receipt()` respects it.

Discriminating any other way was considered and rejected: the button's canned answer text
("approved & applied the proposed config change") is a weak proxy, and `config-optimizer`
already reads that string — making it load-bearing inside the engine would freeze a UI
string into a contract.

The signal is the CLIENT's claim, made by the same code path that verified the endpoint's
applied-field list before claiming anything. The server does not re-read the target's config
to confirm it: a patch spans three surfaces (a routine, a conversation, the instance) with
nested bodies and live-vs-next-run fields, so a comparison would be a second, divergent
notion of "applied". What the record states is what the control that applied it reported.

Lives at the top level, not under `web/`: the web layer WRITES these fields and the engine
READS them (`engine/inbox_questions`, `engine/composer`, `engine/control`), and `engine/`
imports nothing from `web/`. `web/api_questions.py` is also at its size ceiling, where an
addition belongs in its own file (CLAUDE.md Standards; tests/size_ratchet.json).
"""

from __future__ import annotations

#: The answer-file / API-row fields. Named here because four modules spell them.
APPLIED = "config_patch_applied"
TARGET = "config_patch_target"
NOTE = "config_patch_note"

#: What the person who just answered reads (the HTTP response renders it on the card).
NOT_APPLIED_NOTE = ("the answer was recorded; the proposed config change was NOT applied. "
                    "Answering never applies a patch — use the card's `approve & apply` "
                    "button, which is still available.")
APPLIED_NOTE = ("the proposed config change was applied before this answer was filed — the "
                "live value is the patched one.")


def receipt(match: dict, *, applied: bool = False) -> dict:
    """The patch fields an answer to `match` must carry, or `{}` when it carries no patch.

    `applied` is the answer POST's explicit signal: the apply button sets it after its PATCH
    verified every key landed, and an ordinary answer leaves it false — answering never
    applies a patch. Said on the RECORD (what the run reads) and in the RESPONSE (what the
    person who clicked sees), because a silent non-application is indistinguishable from an
    applied one.
    """
    patch = match.get("config_patch")
    if not isinstance(patch, dict) or not patch:
        return {}
    # R2943: `config_target` FIRST, because `engine/config_bridge.patch_target` POPS `routine`
    # out of the patch body at ask time — so by the time any receipt is written that key is gone
    # and `match["routine"]` (the ASKER) was named instead of the target. Measured live: two
    # applied `fau` / `steward-hub-maintainer` patches were reported to config-optimizer as
    # "NOT APPLIED (config-optimizer is unchanged)". `config_target` is the same field the apply
    # button resolves against (static/components/configproposal.js), so the receipt and the
    # button can no longer disagree.
    target = str(patch.get("routine") or match.get("config_target")
                 or match.get("routine") or "")
    return {APPLIED: bool(applied), TARGET: target,
            NOTE: APPLIED_NOTE if applied else NOT_APPLIED_NOTE}


def carry(pair: dict, answer: dict) -> dict:
    """Copy the patch verdict from an answer FILE onto the answer pair a run consumes.

    Exactly how `request`/`decision` already ride a pair (`engine/inbox_questions`): the pair
    is what both delivery seams read — the boot digest and the live turn boundary — so a fact
    missing here reaches no run at all. Absent when the answer carried no verdict, so an
    ordinary answer gains nothing and no reader learns to expect it.
    """
    if APPLIED in answer:
        pair[APPLIED] = bool(answer[APPLIED])
        if answer.get(TARGET):
            pair[TARGET] = str(answer[TARGET])
    return pair


def digest_line(pair: dict) -> str:
    """The line the asking RUN reads under its answer, or "" — the point of the whole module.

    Written to be impossible to mistake for its opposite: this is read mid-run by a model
    deciding whether it may plan on the new value. NOT APPLIED names what to do instead
    (read the live value) and what still can apply it; APPLIED says so plainly and names the
    routine whose config now holds it.
    """
    if APPLIED not in pair:
        return ""
    where = str(pair.get(TARGET) or "").strip()
    if pair[APPLIED]:
        return ("config_patch APPLIED — the proposed change is live"
                + (f" on {where}" if where else "") + "; plan on the new value.")
    return ("config_patch NOT APPLIED — read the live value before planning on it"
            + (f" ({where} is unchanged)" if where else "")
            + "; the operator's \"approve & apply\" button is still available.")


def pending_suffix(question: dict) -> str:
    """What a run reads under its own question that is STILL OPEN and carries a patch, or "".

    The operator's ruling of 2026-10-23 (q-20261023-130002-345, option 3): *"a prose answer
    never applies, but what the run reads names the one-click action that would"*. R2189 keeps
    prose out of the config-writing path; `digest_line` tells the run what happened AFTER an
    answer. Between them sat the question still open, which reached the run as bare prose — so
    a run whose own proposal was waiting could not tell that exactly one control applies it,
    and that waiting for an answer to do it is waiting for something that cannot happen.

    Deliberately not a verdict: `APPLIED`/`NOT APPLIED` judge an answer that exists, and a
    pending patch is neither. It says what is still true (the live value is the old one) and
    names the control by the label the operator sees on it
    (`static/components/configproposal.js`), so neither reader is describing a different thing.
    """
    patch = question.get("config_patch")
    if not isinstance(patch, dict) or not patch:
        return ""
    where = str(patch.get("routine") or question.get("routine") or "").strip()
    return ("\n  carries a config_patch, NOT yet applied"
            + (f" ({where} still holds the old value)" if where else "")
            + ' — answering never applies it; only the "approve & apply" button does, on the '
              "Decisions page or on the question card itself (F807).")


def digest_suffix(pair: dict) -> str:
    """`digest_line` as an indented continuation line, or "" — for a renderer appending it
    under an existing `A:` line (the state digest, the mid-run answer injection).
    """
    line = digest_line(pair)
    return f"\n  {line}" if line else ""
