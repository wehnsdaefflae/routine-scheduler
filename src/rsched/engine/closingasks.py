"""The CLOSING asks a finishing run leaves behind — D212 increment (b).

Increment (a) (0.465.0) made every decision record carry its CLASS, `blocking` or `closing`
(`engine/askclass.py`), from the operator's answer: *"Both axes: subject decides the default, the
run may override it when the work is genuinely blocked"*. It recorded the fact and nothing read
it — `grep '"class"'` over `src/` and `static/` found only the writers. This is its first reader.

WHAT A CLOSING ASK IS, and why the finish is where it belongs. A `blocking` ask is one the run
could not do its work without; it reaches the operator while the run is still going, and the
Decisions page is the right surface for it. A `closing` ask is the opposite: the run finished the
job and asks anyway — "which of these two shapes do you want next time", "should I keep doing X".
Nothing is waiting on it. Its natural reader is the person reading what the run DID, at the moment
he reads it, with the answer one click away — not a card on a page he visits separately, where it
sits beside blocking decisions and competes with them for the same attention. His own words for
the axis are in `askclass`; this module is the surface half of them.

WHAT IT DOES NOT DO. It does not file, answer, re-ask or expire anything: the record is the
record, written by `inbox_questions.file_question` and answered through the one answer route every
surface already uses. This only SELECTS — which of this run's own records a reader of the finish
should be shown — and it never changes what the run does, which is why it sits in the close-out
and not in the finish gate.

THREE BOUNDS, each from something that is already true of the store:

1. **This run's own asks only.** A record's qid is `q-<run-ts>-<turn>`
   (`engine/interact._free_qid`), and `inbox_questions.take_answer` already attributes a record to
   its filing run by exactly that prefix. A run's finish must not advertise another run's open
   questions: that surface belongs to the Decisions page, which shows the whole fleet's.
2. **Still open, and not already answered.** `open_questions` flags a record whose answer is
   waiting in the inbox (`answered: True`); such a record has been dealt with and the run's finish
   must not offer it again. A record answered on the Decisions page between the ask and the finish
   is the common case, not an edge one.
3. **A record filed before increment (a) has no `class` key and reads as `closing`.** That is
   increment (a)'s own instruction to its readers ("a reader added in (b)/(c) should default a
   missing `class` to `closing` rather than assume it is present"), and it is the conservative
   direction: such a record is shown beside the summary, where it costs a reader one glance,
   rather than hidden by a key that did not exist when it was written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import askclass
from .inbox_questions import open_questions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .run_context import RunContext

#: What a surface needs about one closing ask, and nothing more. The record itself carries a
#: dozen keys (a config patch, a report id, an access request's entities) that belong to the
#: Decisions page's own rendering; the finish banner offers the question, its options and what
#: happens without an answer, and the ONE answer route handles the rest from the qid.
FIELDS = ("qid", "question", "options", "default")


def of_record(record: dict) -> str:
    """The CLASS this record means — `closing` for one written before the key existed.

    Bound 3, in one place, so the finish and any later reader of the same records cannot
    disagree about what a missing key means.
    """
    value = record.get("class")
    return str(value) if value in (askclass.BLOCKING, askclass.CLOSING) else askclass.CLOSING


def for_run(ctx: RunContext) -> list[dict]:
    """This run's own open CLOSING asks, oldest first — `[]` when there are none.

    Best-effort by construction: a store that cannot be read yields nothing, because a finish
    must never fail over a surface that only adds something to read.
    """
    try:
        records = open_questions(ctx.routine.dir)
    except OSError:
        return []
    prefix = f"q-{ctx.run_ts}-"
    out = []
    for record in records:
        if not str(record.get("qid") or "").startswith(prefix):
            continue                                   # bound 1: another run's ask
        if record.get("answered"):
            continue                                   # bound 2: already dealt with
        if of_record(record) != askclass.CLOSING:
            continue                                   # bound 3 + the class itself
        out.append({key: record[key] for key in FIELDS if record.get(key)})
    return out
