"""A root command warning answered "… from now on": the web writes the routine's new
`root_check` at click time (rsched/rootmodes.py).

Like an access request's forever-decision, the person's click is the one sanctioned config
write — the engine never writes routine.yaml; it only applies the same switch to the live run
it read from the answer (engine/rootcheck.py).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request

from .. import rootmodes
from ..paths import atomic_write_yaml, read_yaml
from .routines_common import _git_commit


def apply_general(request: Request, match: dict, routine_dir: Path, text: str) -> dict:
    """Write the mode a "from now on" option names; the answer-file fields saying so."""
    switch = rootmodes.parse(text) if match.get("type") == rootmodes.QTYPE else None
    if switch is None or switch[1] != "general":
        return {}
    path = routine_dir / "routine.yaml"
    raw = read_yaml(path, {})
    if not isinstance(raw, dict):
        return {}
    raw["root_check"] = switch[0]
    atomic_write_yaml(path, raw)
    _git_commit(request, routine_dir, f"root command check set to {switch[0]} at a warning")
    return {"root_check_saved": switch[0]}
