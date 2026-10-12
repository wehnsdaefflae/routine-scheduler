"""What bounds a report's `detail` ON DELIVERY — and nothing about where it is stored.

The split this module exists to make explicit (D177, the operator 2026-10-11: *"the ledger is
cheap disk, the prompt is the scarce resource, and R2436's lost question would have been
recoverable"*):

- the `reports.jsonl` ROW keeps the detail **whole** — the archive has no budget problem, and
  what a run wrote has to stay readable later;
- the MESSAGE delivered into the target's inbox is **clipped**, because that text rides in
  another run's prompt, where length is the scarce resource.

Before D177 the same cap bit at both write sites, so the dropped text existed nowhere at all.
That is why the marker below can now name the row to read: the rest is actually there.

Nothing here touches the filesystem or the ledger — it is a pure function over text, which is
why it is its own module and not part of `reports`: the ledger's concern is identity, locking
and lifecycle, and the delivery bound is a formatting decision with a different reason to
change (a prompt-budget decision, revisited whenever model context changes).
"""

from __future__ import annotations

#: The cap on a DELIVERED detail. A prompt-budget decision, unchanged by D177 — what changed is
#: that it no longer governs the stored row.
DETAIL_MAX = 4000

#: What a cut detail says where it lands. F660: the cap used to be a bare slice, so a truncated
#: report read exactly like a complete one — 273 rows of the live ledger end at exactly 4000
#: characters, mid-word, and R2436's cut-off tail WAS the question it was asking, which left
#: that exchange unclosable from the ledger.
#:
#: D177 moved WHERE the cap bites, which is what lets this note name the ledger row for the
#: rest instead of telling the reader to go and ask the filer for text that no longer exists
#: anywhere.
TRUNCATION_NOTE = ("\n\n[TRUNCATED: {dropped} more characters were cut here at the "
                   "{cap}-character cap on a DELIVERED report. The ledger row {item_id} in "
                   ".control/reports.jsonl holds this detail WHOLE — read it there (the "
                   "Messages page shows it, or `rsched items`) if the rest carries a question "
                   "or a conclusion.]")

#: The id placeholder used while MEASURING a clip, before the row's real id is minted. Same
#: width class as a real id, so the reserved width cannot come out short.
_ID_PLACEHOLDER = "R000000"


def clip_detail(detail: str, item_id: str = "") -> tuple[str, int]:
    """The detail as it is DELIVERED, plus how many characters were dropped.

    The ledger row stores the detail whole (D177); this is what rides into the target's
    prompt. `item_id` names the row holding the rest — the filer's own observation measures
    its clip before an id exists, so the placeholder stands in and the reserved width is the
    same either way.
    """
    text = str(detail or "")
    if len(text) <= DETAIL_MAX:
        return text, 0
    # Reserve the note's WORST-CASE width (the count can never exceed the input's own length)
    # and compute the dropped count once against that reservation. A two-pass version would
    # have to re-measure a note whose width changes with the count's digits, and the two
    # passes can disagree — the row would then state a different figure from the one the filer
    # is told, which is the defect this fix exists to remove.
    shown = item_id or _ID_PLACEHOLDER
    reserve = len(TRUNCATION_NOTE.format(dropped=len(text), cap=DETAIL_MAX,
                                         item_id=_ID_PLACEHOLDER))
    kept = max(0, DETAIL_MAX - reserve)
    dropped = len(text) - kept
    return (text[:kept] + TRUNCATION_NOTE.format(dropped=dropped, cap=DETAIL_MAX,
                                                 item_id=shown), dropped)
