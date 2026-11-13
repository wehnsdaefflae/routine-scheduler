"""The state digest's HISTORY tails, bounded in characters instead of in lines.

Goal b5 (the operator, 2026-11-12): *"Our composed prompts … way too long! … move contracts
from the prompt to the engine, and target a 90% routine and conversiaton prompt reduction with
increased contract adherence."* Steps 1 and 2 removed the contract the wire already carried.
This is step 3, and its subject is different: nothing here is a duplicate of anything. It is
the routine's own HISTORY, and the question is how much of it rides every turn.

MEASURED over 37 live captures (`runs/<ts>/prompt-sections.json`, 2026-11-13) — the digest is
the largest section of the composed prompt at **29,122 chars**, and four history tails carry
69% of it:

    block              n     median      p90       max
    ledger            31      7,209    9,159    13,326
    memory index      37      2,666   16,861    23,069
    notes tail        37      5,508    6,267     6,352
    last run result   37      3,861    7,625     9,317

Each was already capped — but in LINES (`notes.tail` 10, `runledger.digest_tail` 30,
`notemeta.INDEX_SHOWN_MAX`). A note line is a paragraph and a ledger entry is a whole run
summary, so a line cap bounds the COUNT and not the size, which is how a "10-line" tail
reaches 6,352 characters.

WHY A CHARACTER BOUND IS HONEST HERE, where it would not be for a contract: every one of
these four is READABLE ON DEMAND and the digest says where — `state/notes.md`, `LEDGER.md`,
`.memory/INDEX.md`, `runs/<ts>/result.md`. Clipping moves a cost from every turn of every run
to the one turn of the one run that actually needs the older half. The marker says what was
dropped and names the file, so the run can always get it back in a single read.

WHAT IS NOT CLIPPED, deliberately: the working plan (the run's own spine — a run that cannot
read its plan re-derives it, which is more expensive than the bytes), the goals and accounting
contract, the finish-line block, and anything stating what the run must DO. Those are
contracts, not history. This module only ever shortens what the run can read back in full.
"""

from __future__ import annotations

#: Characters of history any ONE tail may put in the digest. Measured, not chosen by feel:
#: at 2,500 the four tails together drop ~11,400 chars from the average digest (29,122 ->
#: ~17,700, -39%), while the median notes tail and the median last-run result still fit whole.
#: The caps the four tails had before this were counts of lines, which is why each one reached
#: five to nine thousand characters.
TAIL_BUDGET = 2_500

#: Below this a clip would save nothing worth the marker it costs.
MIN_BUDGET = 200


def clip(text: str, *, budget: int = TAIL_BUDGET, read_with: str = "") -> str:
    """`text`, shortened to `budget` characters on a LINE boundary, with an honest marker.

    The marker names how much was dropped and what to read for the whole — never a bare
    ellipsis, because a run that cannot tell a clipped block from a complete one has to
    re-read the source to find out, which is the cost this is removing.

    The HEAD is kept. Every caller here hands text whose beginning is the part that orients
    (a ledger's heading line, a result's opening summary, an index's first entries); the
    callers that want the newest-last shape slice before they clip.
    """
    if budget < MIN_BUDGET or len(text) <= budget:
        return text
    kept = text[:budget]
    # a whole-line cut: a half-sentence reads as the engine having lost the rest
    if (nl := kept.rfind("\n")) > budget // 2:
        kept = kept[:nl]
    dropped = len(text) - len(kept)
    where = f" — {read_with} holds it whole" if read_with else ""
    return kept.rstrip() + f"\n[... {dropped:,} more characters clipped from this block{where}]"
