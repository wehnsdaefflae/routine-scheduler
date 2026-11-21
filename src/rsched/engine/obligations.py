"""OBLIGATIONS — what a person asked of a ROUTINE that no run has finished, carried until one does.

At the end of every depth-0 ROUTINE run the engine keeps the goals (engine/goals.py) still OPEN
in `state/obligations.json` — engine-owned, so no run can close one by editing the file — and the
next run's ledger starts with them (`source: carried`, `since` the day first taken on). Every
finish accounts for each (engine/accounting.py) until a run MEETS it (verified like any `met`)
or the person drops it in later words (`goal verb=drop`). A carried goal's grounding stands, and
any later word from the person counts as "after" it. The daemon names obligations open longer
than `OVERDUE_DAYS` (daemon/owed_watch.py), so an approval cannot sit unfinished unseen.

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
#: How many settled rows the file keeps — enough to cover any run in flight, not a history.
SETTLED_KEEP = 50


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
             "from_run": str(r.get("from_run") or ""), "waits": str(r.get("waits") or ""),
             "waits_was": str(r.get("waits") or ""),
             "waits_since": str(r.get("waits_since") or "")}
            for r in load(routine_dir)]


def save(ctx: RunContext) -> None:
    """Keep this run's still-open goals for the next run (module docstring). A routine's own
    depth-0 run only; a goal met or dropped this run leaves the file.
    """
    from .runkind import is_conversation

    if ctx.depth > 0 or is_conversation(ctx):
        return
    today = now_iso()[:10]
    path = ctx.routine.dir / FILE
    settled = _settled(path)
    gone = {(r["id"], r.get("from_run")) for r in settled}
    rows = []
    for g in goals.open_goals(ctx.goals):
        row = {"id": g["id"], "text": g["text"], "quote": g["quote"],
               "since": g.get("since") or today, "remains": g.get("remains") or "",
               "from_run": g.get("from_run") or ctx.run_id}
        if (row["id"], row["from_run"]) in gone:
            continue        # the operator settled it on the Decisions page while this ran
        if who := g.get("waits"):
            # the day it FIRST waited on this addressee, so a card is not re-dated each run
            kept = g.get("waits_since") if who == g.get("waits_was") else ""
            row.update(waits=who, waits_since=kept or today)
        rows.append(row)
    if rows or settled or path.exists():
        atomic_write_json(path, {"updated": now_iso(), "open": rows, "settled": settled})


def _settled(path: Path) -> list[dict]:
    """What the operator settled on the Decisions page (`settle`), kept so a run that carried
    the obligation before his click cannot write it back at its finish.
    """
    data = read_json(path)
    rows = data.get("settled") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict) and r.get("id")] if isinstance(rows, list) \
        else []


def settle(routine_dir: Path, ob_id: str, from_run: str, *, how: str, note: str = "") -> bool:
    """The operator's decision on an obligation that WAITED ON HIM: `met` or `dropped`. The
    row moves from `open` to `settled` (so an in-flight run cannot resurrect it). False when no
    such row.
    """
    path = Path(routine_dir) / FILE
    rows = load(routine_dir)
    hit = next((r for r in rows if r["id"] == ob_id and r.get("from_run") == from_run), None)
    if hit is None:
        return False
    settled = _settled(path)
    settled.append({"id": ob_id, "from_run": from_run, "text": hit.get("text", ""),
                    "how": how, "by": "operator", "at": now_iso(), "note": note})
    atomic_write_json(path, {"updated": now_iso(), "open": [r for r in rows if r is not hit],
                             "settled": settled[-SETTLED_KEEP:]})
    return True
