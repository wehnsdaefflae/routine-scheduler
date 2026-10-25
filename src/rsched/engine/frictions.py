"""What went WRONG in one run, as SIGNATURES a change can be judged by (the moment test).

The change measurement (readmodels/change_effects.py) compares the runs before a change with the
runs after it on rates — failed runs, claims challenged, tokens. A routine's input differs from
run to run, so a rate needs many runs to move past its noise, and on this instance a recipe
changes about every second run: 96% of all changes ended `too few runs`. Harness² (arXiv
2610.14224) judges an edit differently — "an edit earns its place when a SPECIFIC MOMENT in a
trajectory shows the mechanism": a failure that REPEATED across the base runs is a real property
of the harness, and the question is whether THAT failure came back, not whether an average moved.

A signature is that failure, named the same way in every run of every routine, read from the
events the engine already writes — nothing here is a new judgement about a turn:

- `schema` — a reply that was not a valid action (an `error` at `where: schema`);
- `script:<name>` / `util:<name>` — that script or util exited non-zero;
- `edit_miss` — an `edit_file` whose anchor was not found or not unique;
- `read_miss` — a `read_file` of a path that is not there;
- `refused:<kind>` — any other error that action came back with (a gate, a missing grant, an
  edit that would break a JSON file);
- `memory_cap` — a `memory_write` refused by the note cap;
- `deferral:<rung>` — the finish was set aside by that rung of the finish gate;
- `budget:<resource>` — that budget was spent and the run took its reserved finish turn;
- `unmet:d<n>` — the run's final accounting left that Done-when line unmet.

Two things are deliberately NOT signatures. A `shell` command's non-zero exit is how `grep`
says "no match", so it names no failure. An endpoint error or a classifier refusal is the
TRANSPORT failing, which no change to a routine can address. A goal (`b<n>`) is one person's
request to one run, so it never recurs and is not counted either.

Each signature is counted per run (how often it happened). The read model asks only whether it
happened (readmodels/change_frictions.py): one run that tripped over the same anchor nine times
is one run with an `edit_miss`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from .transcript import _open_bytes

#: The finish gate's rungs by the key each one records on its deferral (engine/finishgate.py
#: `_defer`). A deferral carrying none of them is the "no action at all" rung.
RUNGS = ("pending_user_input", "asked_back", "final_undeclared", "jobs_owed", "accounting",
         "assist", "unbacked_claims", "claims_unsupported")
#: Kinds whose non-zero exit or error is not a failure of the run's harness (module docstring).
NOT_COUNTED = frozenset({"shell", "finish", "reminder_hold", "assist_hold"})


def _observation(payload: dict) -> str | None:
    kind = str(payload.get("kind") or "")
    if kind == "finish" and payload.get("rejected"):
        rung = next((r for r in RUNGS if payload.get(r)), "no_action")
        return f"deferral:{rung}"
    if kind in NOT_COUNTED:
        return None
    exit_code = payload.get("exit")
    if kind in ("script", "util") and isinstance(exit_code, int) and exit_code != 0:
        return f"{kind}:{payload.get('name') or '?'}"
    if not payload.get("error"):
        return None
    error = str(payload["error"])
    if kind == "edit_file" and "anchor" in error:
        return "edit_miss"
    if kind == "read_file" and "No such file" in error:
        return "read_miss"
    if kind == "memory_write" and "capped" in error:
        return "memory_cap"
    return f"refused:{kind or '?'}"


def from_events(events: Iterable[dict]) -> dict[str, int]:
    """Every signature in one run's events, with how often it happened."""
    found: Counter[str] = Counter()
    unmet: list[str] = []
    for ev in events:
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = ev.get("type")
        if kind == "observation":
            if sig := _observation(payload):
                found[sig] += 1
        elif kind == "error" and payload.get("where") == "schema":
            found["schema"] += 1
        elif kind == "user_injection" and payload.get("budget"):
            found[f"budget:{payload['budget']}"] += 1
        elif kind == "stopping_update":
            judged = payload.get("judged")
            if isinstance(judged, dict):     # the LAST update is the run's final accounting
                unmet = [str(i) for i, v in judged.items()
                         if v == "unmet" and str(i).startswith("d")]
    found.update(f"unmet:{i}" for i in unmet)
    return dict(sorted(found.items()))


def _lines(path: Path) -> Iterator[dict]:
    """The transcript's events, streamed — a 700-turn transcript is never held whole. The plain
    file or its rotated `.gz`, read as `read_events` reads them; a torn line is skipped.
    """
    with _open_bytes(path) as fh:
        for raw in fh:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if isinstance(ev, dict):
                yield ev


def of_run(run_dir: Path) -> dict[str, int] | None:
    """One run's signatures from its transcript, or None when there is no transcript to read —
    UNKNOWN, never an empty dict: a run with no record of what happened did not run cleanly.
    """
    path = run_dir / "transcript.jsonl"
    if not path.exists() and not path.with_suffix(".jsonl.gz").exists():
        return None
    return from_events(_lines(path))
