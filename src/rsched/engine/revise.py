"""Revise-recipe: a run-scoped unlock that lets a run edit its OWN recipe when the user asks
for a change from the run view ("Revise recipe" beside the message box).

The web `/revise` endpoint drops a marker in the finished run's dir + injects the framed
instruction, then resumes the run. The turn loop reads the marker ONCE at init and, for that
leg only, grants recipe self-write (`recipe_unlocked`) plus the file-edit kinds — so the
orchestrator can read and rewrite its own `main.md` / `stages/` / `traits/` / `tuning.yaml`
with `edit_file`/`write_file`, even when the routine's workflow `tools:` list omits them.

No persisted `fs_write_root` is involved, so the recipe stays sealed to every ORDINARY run —
the unlock is exactly the leg the user requested (the marker is cleared on read). `routine.yaml`
(config) is NOT unlocked: the `_write_gate` still blocks it, and a config-shaped request is
routed to a deferred `ask_user` the Decisions page can apply.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import atomic_write_json, read_json

REVISE_MARKER = "revise.json"
# The file-edit kinds a revise leg needs on top of its normal tools, so it can read and
# rewrite its own recipe even when the routine's workflow `tools:` list omits them.
REVISE_KINDS = ("read_file", "write_file", "edit_file")


def write_revise_marker(run_dir: Path, instruction: str) -> None:
    """Drop the marker the loop reads at init (web layer, before resume)."""
    atomic_write_json(run_dir / REVISE_MARKER, {"instruction": instruction})


def revise_marker(run_dir: Path) -> dict | None:
    """The revise request for this run, if the endpoint left one — `{instruction: ...}`."""
    obj = read_json(run_dir / REVISE_MARKER)
    return obj if isinstance(obj, dict) else None


def clear_revise_marker(run_dir: Path) -> None:
    """One-shot: the unlock covers exactly the leg the user asked for, nothing after."""
    try:
        (run_dir / REVISE_MARKER).unlink(missing_ok=True)
    except OSError:
        pass
