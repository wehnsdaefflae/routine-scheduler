"""OBLIGATIONS — what a person asked of a ROUTINE that no run has finished, carried until one does.

A routine run's goals (engine/goals.py) used to be that run's alone: the operator's brief, a
request mid-run, an approval given on the Decisions page — and when the run ended with one unmet,
the next run started from nothing. On 2026-07-30 the operator approved freelance-radar's profile
update ("You fix all of that"); it was never applied, and eleven weeks later nothing on the
instance knew it was owed. The approval had been read, acknowledged in a summary, and forgotten.

So at the end of every depth-0 ROUTINE run the engine keeps the goals still OPEN in
`state/obligations.json` — engine-owned, so no run can close one by editing the file
(engine/fileops.py) — and the next run's ledger starts with them (`source: carried`, `since` the
day a run first took them on) beside its brief. Every finish accounts for every open goal
(engine/accounting.py), so each run answers for each obligation until one MEETS it (the claim is
checked against its transcript like any `met`, engine/verifier.py) or the person withdraws it in
words of their own (`goal verb=drop`). A carried goal's grounding stands — its quote was found in
what the person wrote to the run that took it on — and any word the person writes later is "after"
it. The daemon names every obligation open longer than `OVERDUE_DAYS` on the blocked-work surface
(daemon/owed_watch.py), so an approval cannot sit unfinished without someone seeing it.

A conversation keeps its goals in its own transcript across replies and needs none of this.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..ids import now_iso
from ..paths import atomic_write_json, read_json
from . import goals

if TYPE_CHECKING:
    from .run_context import RunContext

FILE = Path("state") / "obligations.json"
REFUSAL = ("state/obligations.json is the engine's record of what a person asked of this "
           "routine and no run has finished — meet one through your finish `accounting`, or "
           "drop it with `goal` verb=drop quoting the person's own later words")
#: How long an obligation may stay open before the daemon names it as owed work.
OVERDUE_DAYS = 7


def load(routine_dir: Path) -> list[dict]:
    """The open obligations on file, oldest first."""
    data = read_json(Path(routine_dir) / FILE)
    rows = data.get("open") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict) and r.get("id")] if isinstance(rows, list) \
        else []


def carried(routine_dir: Path) -> list[dict]:
    """The ledger entries a fresh run starts with: every open obligation, as an open goal whose
    grounding lies in an EARLIER run (`heard: -1`, so any word the person writes now is later).
    """
    return [{**goals.blank(str(r["id"]), str(r.get("text") or ""), str(r.get("quote") or ""),
                           source="carried", heard=-1, turn=0),
             "since": str(r.get("since") or ""), "remains": str(r.get("remains") or ""),
             "from_run": str(r.get("from_run") or "")}
            for r in load(routine_dir)]


def save(ctx: RunContext) -> None:
    """Keep this run's still-open goals for the next run (module docstring). A routine's own
    depth-0 run only; a goal met or dropped this run leaves the file.
    """
    from .runkind import is_conversation

    if ctx.depth > 0 or is_conversation(ctx):
        return
    today = now_iso()[:10]
    rows = [{"id": g["id"], "text": g["text"], "quote": g["quote"],
             "since": g.get("since") or today, "remains": g.get("remains") or "",
             "from_run": g.get("from_run") or ctx.run_id}
            for g in goals.open_goals(ctx.goals)]
    path = ctx.routine.dir / FILE
    if rows or path.exists():
        atomic_write_json(path, {"updated": now_iso(), "open": rows})
