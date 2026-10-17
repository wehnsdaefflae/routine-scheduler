"""The setup surface's OUTCOME row — does this routine say, and measure, what it exists to produce?

A recipe whose `## Done when` names only process — "every listing was judged", "the page shows
this run's state" — can be met in full by a run that produces nothing. freelance-radar met
every line of its Done when for four months while 89 applications won zero interviews, and
grants-radar for 14 runs while it found no new programme. A process line is not wrong; a
recipe of ONLY process lines has no line a run can fail by failing its principal.

So a routine answers three questions here, each a gap until it is answered, and each a BLOCKS
row — `rsched validate` fails on it:

1. does routine.yaml declare an OUTCOME contract (config/outcome.py) — who the results are for
   and the numbers that say they came?
2. does `scripts/outcome.py` exist to compute them from the routine's own state
   (engine/outcome.py runs it at every run's end)?
3. does a `## Done when` line NAME one of those measures, by its id — so every run answers, in
   its accounting, for the outcome and not only for its own process?

The remedy is one kind (`declare_outcome`), its `missing` naming the half still absent.
Conversations are skipped (a conversation's goals are its person's words, engine/goals.py), and
so is a routine that starts no run — switched off or retired: it produces nothing to measure —
and one with no `main.md`, which has no Done when to judge (the loader reports that already).
The row never reaches a run's BOOT note (engine/boot.py): it fails no call the run makes and no
run can declare its own success; it is the operator's and the improver's to close.
The row is the ROUTINE's own (its config and its recipe), never the library's, so its `source`
says `recipe` and the library watch never files it as drift (daemon/library_watch.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..engine import donewhen
from .surface_nodes import BLOCKS, _node

_SOURCE = {"recipe": "outcome"}
_EFFECT = ("nothing a run does can fail its principal: every judge reads the routine's own "
           "account, which is how 89 applications with no interview read as healthy")


def _is_conversation(server: Any, routine_dir: Path) -> bool:
    conversations = getattr(server, "conversations_home", None)
    try:
        return conversations is not None and \
            routine_dir.resolve().parent == Path(conversations).resolve()
    except OSError:
        return True


def names_a_measure(text: str, measure_ids: list[str]) -> bool:
    """Does a Done-when line's text name one of the measures by its id, as a word?"""
    return any(re.search(rf"(?<![\w-]){re.escape(mid)}(?![\w-])", text, re.IGNORECASE)
               for mid in measure_ids)


def _outcome_nodes(server: Any, cfg: Any) -> list[dict]:
    routine_dir = Path(getattr(cfg, "dir", None) or server.routines_home / cfg.slug)
    if _is_conversation(server, routine_dir) or not getattr(cfg, "enabled", True):
        return []
    if not (routine_dir / "main.md").is_file():
        return []                     # no recipe to judge — the loader already says so
    from ..engine import finishline

    if finishline.goal_reached(routine_dir):
        return []
    contract = getattr(cfg, "outcome", None)
    if contract is None:
        return [_node("recipe:outcome", "undeclared", BLOCKS,
                      "it declares no outcome — nothing says, as a number, what it exists to "
                      "produce for its principal", _EFFECT,
                      {"kind": "declare_outcome", "missing": "an outcome contract in "
                       "routine.yaml"}, _SOURCE)]
    if not (routine_dir / "scripts" / "outcome.py").is_file():
        return [_node("recipe:outcome", "unmeasured", BLOCKS,
                      "it declares an outcome but has no scripts/outcome.py to measure it",
                      "every run records an outcome error instead of a reading",
                      {"kind": "declare_outcome", "missing": "scripts/outcome.py"}, _SOURCE)]
    ids = [m.id for m in contract.measures]
    lines = donewhen.read(routine_dir)
    if not any(names_a_measure(line["text"], ids) for line in lines):
        return [_node("recipe:outcome", "process-only", BLOCKS,
                      "no `## Done when` line names one of its measures "
                      f"({', '.join(ids)})", _EFFECT,
                      {"kind": "declare_outcome",
                       "missing": "a Done-when line naming one of its measures"}, _SOURCE)]
    return []
