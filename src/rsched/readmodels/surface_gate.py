"""The setup surface's GATE rows — a check of the routine's own gate that never lets a fire skip.

A fire is skipped only when every check answers "no work", so a check that answered "work" on
every one of its last asks has kept the gate from ever skipping (`gate_yield.py`). The row says
so with the count and the check's newest reason, which is usually the diagnosis — a queue no
run drains, a state key that does not exist, a value the idle list does not know — and names the
remedy, `edit_gate`: narrow that check or drop it, in the routine's Run gate section.

A NOTE: the gate costs seconds per fire and fails nothing, and the work it admits may be real.
It is the routine's own row (`source.gate`), so it never reaches a run's boot note (a NOTE never
does) and the library watch never files it as drift. Skipped for a conversation, a routine that
starts no run, and a routine with no gate switched on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gate_yield
from .surface_nodes import NOTE, _node
from .surface_outcome import _is_conversation


def _gate_nodes(server: Any, cfg: Any) -> list[dict]:
    gate = getattr(cfg, "run_gate", None)
    if gate is None or not gate.enabled or not getattr(cfg, "enabled", True):
        return []
    routine_dir = Path(getattr(cfg, "dir", None) or server.routines_home / cfg.slug)
    if _is_conversation(server, routine_dir):
        return []
    return [_node(f"gate:{c['id']}", "never-idle", NOTE,
                  f"its gate check {c['id']} ({c['kind']}) answered work on all {c['asked']} "
                  f"of its last asks — newest: {c['last']}",
                  "a check that always finds work never lets a fire skip, so the gate saves "
                  "nothing while it stands",
                  {"kind": "edit_gate", "check": c["id"]}, {"gate": c["id"]})
            for c in gate_yield.reading(routine_dir)["never_idle"]]
