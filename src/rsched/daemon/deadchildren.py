"""The restart recovery note's CHILD RUNS clause — READ off disk, never asserted.

The note a restart resume files used to tell the leg that "any CHILD RUN that was in flight
did not survive, so re-issue what you still need" — a sentence composed without looking. The
dead child's directory is sitting in the same run dir (`sub/<n>/transcript.jsonl`, appended
turn by turn by `engine/childrun.py`), so the leg can be told how far each child actually got
and where to read it, instead of being sent to redo work that is on disk.

SALVAGE, not recovery, in exactly the sense `engine/subsalvage.py` means it for a resumed leg:
nothing is re-adopted, nothing new is stored, the directories are the record. This is the
DAEMON's side of the same event — it composes one note before the resume, not the `children`
rows a live leg reads, so it lives here.

Two kinds of child directory are deliberately left out: one with no recorded turn (there is
nothing to report, and "reached turn 0" reads as a finding) and one whose transcript holds a
`finish` (it got where it was going; its hand-back is in its own dir either way).
"""

from __future__ import annotations

import json
from pathlib import Path

#: How much of a child's last `say` the note carries. Enough to recognise the step it died on;
#: the transcript path is in the same line for everything else.
SAY_CAP = 160


def _events(path: Path):
    """Every decodable JSON object in a JSONL file, best-effort.

    A transcript cut mid-write (which is exactly how these end) must not cost the leg the
    whole clause, so an undecodable line is skipped and a missing file yields nothing.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except OSError:
        return


def _labels(run_dir: Path) -> dict[int, str]:
    """The label each child was spawned with, from the PARENT's `child_start` events.

    The parent transcript is the only place a label survives: a child's own header records its
    run id and workflow, never the name the parent called it.
    """
    out: dict[int, str] = {}
    for ev in _events(run_dir / "transcript.jsonl"):
        payload = ev.get("payload") or {}
        if ev.get("type") == "child_start" and isinstance(payload.get("n"), int):
            out[payload["n"]] = str(payload.get("label") or "")
    return out


def _progress(child_dir: Path) -> tuple[int, str, bool]:
    """(highest turn recorded, the `say` of that turn, whether the child finished).

    An orphan is never collected, so its own transcript is the only record of either number —
    the same reason `subsalvage.spend_reached` reads spend from there.
    """
    turns, say, finished = 0, "", False
    for ev in _events(child_dir / "transcript.jsonl"):
        if ev.get("type") == "finish":
            finished = True
        elif (ev.get("type") == "assistant_action" and isinstance(ev.get("turn"), int)
              and ev["turn"] >= turns):
            turns = ev["turn"]
            say = str((ev.get("payload") or {}).get("say") or "")
    return turns, say, finished


def _line(n: int, label: str, turns: int, say: str, transcript: Path) -> str:
    """One dead child as the resumed leg reads it: what it was, how far it got, where it is."""
    named = f" ({label})" if label else ""
    step = say[:SAY_CAP] + "…" if len(say) > SAY_CAP else say
    told = f"its last recorded step was {step!r}; " if step else ""
    return (f"- child `sub/{n}`{named} reached turn {turns}; {told}"
            f"its transcript is at {transcript} and you can read it")


def note_clause(run_dir: Path) -> str:
    """What the recovery note says about the children the killed leg left behind.

    Empty when nothing on disk says a child was in flight: a note volunteering a paragraph
    about children the run never started is the same defect pointing the other way.
    """
    try:
        dirs = sorted((p for p in (run_dir / "sub").iterdir() if p.name.isdecimal()),
                      key=lambda p: int(p.name))
    except OSError:
        return ""
    labels = _labels(run_dir)
    lines = []
    for child_dir in dirs:
        n = int(child_dir.name)
        turns, say, finished = _progress(child_dir)
        if finished or not turns:
            continue
        lines.append(_line(n, labels.get(n, ""), turns, say,
                           child_dir / "transcript.jsonl"))
    if not lines:
        return ""
    return ("\n\nIts CHILD RUNS did not survive either — a child is a thread inside the "
            "process that died, so there is none left to wait on, to kill or to re-adopt. "
            "What they did is NOT gone; this is what their directories say right now:\n"
            + "\n".join(lines)
            + "\nRead those transcripts first and re-issue only what they do not already "
              "give you.\n")
