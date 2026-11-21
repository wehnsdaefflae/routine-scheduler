"""What CLASS of question a decision record is — blocking or closing (D212).

The class is ORTHOGONAL to `mode` (answers take hours, so "the process waits" is rare):

  mode   — does THIS run's process sit and wait for the answer (with the ask timeout)?
  class  — can the run do the work it was fired to do without the answer at all?

The class lets every surface rendering the record sort the operator's queue by what is actually
holding work up. The rule:

* the SUBJECT sets the default — a typed access `request` (the run cannot reach the entity at
  all) defaults to `blocking`, a plain question to `closing`;
* the RUN may override per question with `blocks_work: true` — *without this answer the run
  cannot do the work it was fired to do*.

The override only goes one way: a run cannot talk itself out of its own access blocker.
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
