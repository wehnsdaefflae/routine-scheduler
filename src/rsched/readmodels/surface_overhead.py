"""The setup surface's PROCESS-OVERHEAD row — do this routine's runs go to its task?

The operator's standard (2026-11-06): a routine whose runs are mostly about bookkeeping — its own
ledger, state, records, reconciliation — rather than its task is a critical failure. Every run
measures the split (`engine/overhead.py`), and `readmodels/overhead_reading.py` reads the
routine's median over its last runs against the fleet bar. Over the bar, this row says so, with
the figure, and names the remedy: revise the RECIPE, which is where the bookkeeping is built in
(an orient and a record stage, a LEDGER, Done-when lines that name a record) — no setting
removes it.

A NOTE, not a blocking row: nothing a run calls fails because of it, and `rsched validate` must
not go red on a measurement. It is UNMET, so it carries a fix (`revise_recipe`). It is the
routine's own row (`source.recipe`), so it never reaches a run's boot note — the run is told at
the one place it can act, the FINISH block's recipe-revision ask (engine/finish_digest.py) —
and the library watch never files it as drift. A conversation (no recipe to revise) and a routine
that starts no run are skipped — the latter's last runs describe a recipe nothing is running.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import overhead_reading
from .surface_nodes import NOTE, _node
from .surface_outcome import _is_conversation


def _overhead_nodes(server: Any, cfg: Any) -> list[dict]:
    routine_dir = Path(getattr(cfg, "dir", None) or server.routines_home / cfg.slug)
    if _is_conversation(server, routine_dir) or not getattr(cfg, "enabled", True):
        return []
    r = overhead_reading.reading(Path(server.routines_home), cfg.slug)
    if r is None or not r["over"]:
        return []
    share = f"{round(100 * r['median'])}%"
    return [_node("recipe:overhead", "process-dominated", NOTE,
                  f"its runs are dominated by its own process: {overhead_reading.describe(r)}",
                  "most of every run is spent keeping the routine's own records instead of "
                  "doing its task",
                  {"kind": "revise_recipe", "share": share}, {"recipe": "overhead"})]
