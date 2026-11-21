"""The CLOSING asks a finishing run leaves behind — D212 increment (b).

A `blocking` ask (`engine/askclass.py`) is one the run could not work without; it belongs on the
Decisions page while the run goes on. A `closing` ask is made after the job is done and nothing
waits on it, so its natural reader is the person reading what the run DID, at the finish, with
the answer one click away.

This only SELECTS which of this run's own records the finish shows — it files, answers and
expires nothing, and never changes what the run does (so it sits in the close-out, not the
finish gate). THREE BOUNDS:

1. **This run's own asks only** — qid prefix `q-<run-ts>-` (`engine/interact._free_qid`).
2. **Still open, and not already answered** (`open_questions`' `answered: True`).
3. **A record with no `class` key reads as `closing`** — the conservative direction: shown
   beside the summary rather than hidden.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import askclass
from .inbox_questions import open_questions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .run_context import RunContext

#: What a surface needs about one closing ask; the answer route handles the rest from the qid.
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
