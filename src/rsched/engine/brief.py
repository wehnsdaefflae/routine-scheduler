"""The JOB BRIEF — one line the operator gives a run they start by hand: what THIS run is for.

"Run now" and "answer & run now" take it. A briefed run answers for the brief instead of its
recipe's `## Done when` — one accounting entry, `b1 met: <evidence>` or `b1 unmet: <what
remains>`, checked like any other — because a run started for one job would otherwise be held
to the routine's whole list and account "not due" for every line of it. The finish line is
the routine's and still owed a distance. A follow-up reply after a finish is already a brief by
construction (`finishgate._owed` owes nothing there).

The runner writes it into the run dir before the engine starts (`brief.json`); the engine reads
it ONCE at boot, so it is part of the composed prompt from the first turn and never changes
under a run.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import atomic_write_json, read_json

FILE = "brief.json"
MAX_CHARS = 300
ID = "b1"


def write(run_dir: Path, text: str) -> None:
    atomic_write_json(Path(run_dir) / FILE, {"brief": " ".join(text.split())[:MAX_CHARS]})


def read(run_dir: Path) -> str:
    doc = read_json(Path(run_dir) / FILE)
    return str(doc.get("brief") or "").strip() if isinstance(doc, dict) else ""


def owed(text: str) -> list[dict]:
    """The one line a briefed run accounts for, in the shape of a Done-when line."""
    return [{"id": ID, "stage": "", "text": text}]
