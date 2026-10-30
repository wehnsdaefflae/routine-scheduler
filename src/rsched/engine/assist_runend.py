"""The RUN-END predicates — what a rule may ask as a run closes, for the run that comes next.

Its own module for the reason `assist_child.py` and `assist_outcome.py` are:
`assist_predicates.py` is the registry and is AT the size bar, so a predicate that needs more
than its registry entry lives beside it and is imported in.

This is the hook point `run-end` (`hooks/points.py`), fired by `engine/loopend.finish_run`
after `result.md` is written. It is the first point whose CARRIER is `prompt_section` rather
than an observation or an engine note: there is no model left to tell, so what fires here
reaches the NEXT run — through `state/notes.md`, the channel that run's state digest already
renders. A predicate here therefore answers one question only: *what does the next run need to
know that this run's summary will not carry?*

What it may look at is the ENDING as the engine recorded it — the status and what the close-out
had to do — and never the transcript: a line written for a run that does not exist yet has to
be true from the record, not from a reading of how this run felt.
"""

from __future__ import annotations

from typing import Any


def children_killed_at_end(s: Any) -> bool:
    """This run finished while children were still working, so they were terminated.

    evidence-discipline's moment at the close-out. A child terminated by its parent's finish
    (`subruns.kill_all`) has done work that IS on disk — in `runs/<id>/sub/<n>/` and in
    whatever tree it was building — and nothing in the next run's prompt says so. The engine's
    own resume note asserted the opposite ("results lost … Re-issue any you still need") until
    F778, and a run that believed it threw finished work away twice in two days: ~1,400 s × 2
    on 2026-10-25 and 69 turns on 2026-10-26, re-issuing briefs whose output already existed.

    The count is what the close-out itself measured, so the predicate asserts nothing the
    engine did not already record. False when nothing was running at the end, which is every
    ordinary run — the layer must cost nothing in the common case.
    """
    ending = s.ending
    if not isinstance(ending, dict):
        return False
    try:
        return int(ending.get("killed") or 0) > 0
    except (TypeError, ValueError):
        return False
