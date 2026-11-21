"""The RUN-END predicates — what a rule may ask as a run closes, for the run that comes next.

The hook point `run-end` (`hooks/points.py`), fired by `loopend.finish_run` after `result.md`.
No model is left to tell, so what fires reaches the NEXT run through `state/notes.md`. A
predicate reads only the ENDING as the engine recorded it, never the transcript: a line for a
future run must be true from the record.
"""

from __future__ import annotations

from typing import Any


def children_killed_at_end(s: Any) -> bool:
    """This run finished while children were still working, so they were terminated.

    A child killed by the parent's finish (`subruns.kill_all`) left work on disk that the next
    run's prompt does not mention, so it would re-issue it. The count is the close-out's own;
    False in every ordinary run.
    """
    ending = s.ending
    if not isinstance(ending, dict):
        return False
    try:
        return int(ending.get("killed") or 0) > 0
    except (TypeError, ValueError):
        return False
