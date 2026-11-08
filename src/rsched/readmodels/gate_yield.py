"""What a routine's run gate has actually DONE — how often each check answered "no work".

A gate earns its keep only by skipping a fire, and a fire is skipped only when EVERY check
answers "no work". So a check that has answered "work" every time it was asked has never let a
fire skip: while it stands, the gate costs its seconds per fire and saves nothing. That is the
measurement behind the operator's words of 2026-11-08 — *"gates that don't fire are useless"*
— and on that day it named the cause of 117 admitted fires out of 118: a request queue no run
could drain (14 of 14), untested models nobody could test (13 of 13), a phase value the idle
list did not know (7 of 7), a state key that did not exist (4 of 5).

Read off `runs/<ts>/gate.json` through `gate_history.verdict` (memoized per file), over the last
`WINDOW` JUDGED fires — a bypass and an error decided nothing. Two readers: the setup surface
names each always-working ROUTINE check as a note (`surface_gate.py`), and the next-fire gate
block shows a run how its gate has been doing (`engine/gate_digest.py`). A check the last run
wrote is counted apart: its ids are that run's own and change with every gate, so a per-id
rate across them would mean nothing — what they get is their own skip count.
"""

from __future__ import annotations

import re
from pathlib import Path

from .gate_history import verdict

#: How many judged fires a reading spans, and how many asks a check needs before "it always
#: answered work" is a finding rather than an anecdote.
WINDOW = 20
MIN_ASKS = 5

_RUN_DIR = re.compile(r"\d{8}-\d{6}")


def _verdicts(routine_dir: Path) -> list[dict]:
    runs = Path(routine_dir) / "runs"
    if not runs.is_dir():
        return []
    out: list[dict] = []
    for run_dir in sorted((p for p in runs.iterdir() if _RUN_DIR.fullmatch(p.name)),
                          reverse=True):
        v = verdict(run_dir)
        if v and v["decision"] in ("run", "skip"):
            out.append(v)
            if len(out) == WINDOW:
                break
    return out


def reading(routine_dir: Path) -> dict:
    """`{judged, skipped, before_checks, run_written: {fires, skipped}, checks, never_idle,
    last_source, last_rejected}` — the last two from the newest fire whose checks were asked.

    `before_checks` counts fires a built-in reason or the baseline admitted with no check
    asked. `checks` is one row per ROUTINE check asked in the window, `{id, kind, asked, work,
    last}` (`last` the newest reason it gave); `never_idle` is the subset asked at least
    `MIN_ASKS` times that answered work every time — never the `max_quiet` backstop, whose
    "no work" is the normal answer and whose "work" is the one it exists to give.
    """
    rows: dict[tuple[str, str], dict] = {}
    judged = skipped = before = run_fires = run_skips = 0
    last: dict = {}
    for v in _verdicts(routine_dir):
        if not last and v["source"]:
            last = v                         # the newest fire whose checks were asked
        judged += 1
        skipped += v["decision"] == "skip"
        if not v["checks"]:
            before += 1
            continue
        if v["source"] == "run":
            run_fires += 1
            run_skips += v["decision"] == "skip"
            continue
        for c in v["checks"]:
            row = rows.setdefault((c["id"], c["kind"]), {
                "id": c["id"], "kind": c["kind"], "asked": 0, "work": 0, "last": c["reason"]})
            row["asked"] += 1
            row["work"] += bool(c["work"])
    checks = list(rows.values())
    never_idle = [c for c in checks if c["kind"] != "max_quiet"
                  and c["asked"] >= MIN_ASKS and c["work"] == c["asked"]]
    return {"judged": judged, "skipped": skipped, "before_checks": before,
            "run_written": {"fires": run_fires, "skipped": run_skips},
            "checks": checks, "never_idle": never_idle,
            "last_source": last.get("source", ""), "last_rejected": last.get("rejected", "")}
