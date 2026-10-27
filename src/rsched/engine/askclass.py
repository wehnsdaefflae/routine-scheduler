"""What CLASS of question a decision record is — blocking or closing (D212).

Measured over the 282 question records the instance had filed: `mode: "blocking"` was used 0
times, 0% of answers arrived within an hour and the median answer latency was 2.97 h. So the
class can never mean "the run's process waits" — that is `mode`, and the two are ORTHOGONAL:

  mode   — does THIS run's process sit and wait for the answer (with the ask timeout)?
  class  — can the run do the work it was fired to do without the answer at all?

A run files a BLOCKING question and carries on exactly as it files a closing one; what the
class says is whether the asking run was stopped by the gap. Every surface that renders the
record (the Decisions page, the run view) can therefore sort the operator's queue by what is
actually holding work up, instead of by when each card happened to be filed.

The rule is the operator's own, verbatim — *"Both axes: subject decides the default, the run
may override it when the work is genuinely blocked"*:

* the SUBJECT sets the default — a question carrying a typed access `request` (a grant,
  secret or machine entity: the run cannot reach the thing at all) defaults to `blocking`,
  and a plain question defaults to `closing`;
* the RUN may override per question with `blocks_work: true`, whose test is his: *without
  this answer the run cannot do the work it was fired to do*.

The override only ever goes one way. A run cannot declare an access request `closing`: it
asked for the entity because something it meant to do needs it, and a run talking itself out
of its own blocker is the failure the subject default exists to prevent.
"""

from __future__ import annotations

#: The run could not do the work it was fired to do without this answer.
BLOCKING = "blocking"
#: The run finished its work without it; the answer shapes the NEXT run.
CLOSING = "closing"

CLASSES = (BLOCKING, CLOSING)


def of(*, request: list[str] | None = None, blocks_work: object = False) -> str:
    """The class this decision record carries — the hybrid rule above, in one place.

    `request` is the record's typed access-request ids (empty for a plain question) and
    `blocks_work` the asking action's own override, taken for its truthiness so a caller can
    hand over whatever the action carried without normalizing it first.
    """
    return BLOCKING if (request or blocks_work) else CLOSING
