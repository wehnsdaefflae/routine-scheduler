"""The state digest's HISTORY tails, bounded in characters instead of in lines.

The four history tails (ledger, memory index, notes tail, last run result) were the bulk of the
digest, and each was capped in LINES — but a note line is a paragraph and a ledger entry a whole
run summary, so a line cap bounds the count, not the size.

A character bound is honest here because each tail is READABLE ON DEMAND (`state/notes.md`,
`LEDGER.md`, `.memory/INDEX.md`, `runs/<ts>/result.md`) and the marker names the file, so the
run gets the rest back in one read. NOT clipped, deliberately: the working plan, goals and
accounting, the finish-line block, and anything stating what the run must DO — contracts, not
history.
"""

from __future__ import annotations

#: Characters of history any ONE tail may put in the digest — measured: roughly -39% of the
#: average digest while the median notes tail and last-run result still fit whole.
TAIL_BUDGET = 2_500

#: Below this a clip would save nothing worth the marker it costs.
MIN_BUDGET = 200


def clip(text: str, *, budget: int = TAIL_BUDGET, read_with: str = "") -> str:
    """`text`, shortened to `budget` characters on a LINE boundary, with an honest marker.

    The marker names how much was dropped and what to read for the whole — never a bare
    ellipsis. The HEAD is kept (it orients); callers wanting newest-last slice before clipping.
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
