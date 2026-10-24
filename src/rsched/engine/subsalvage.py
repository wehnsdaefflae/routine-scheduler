"""Children a LATER LEG cannot see in memory, salvaged from the directories they left.

F732, filed at the operator's own request (2026-10-19: "maybe we can do sth to at least be
able to salvage some of the data in the future"). `SubrunManager.subruns` is an in-memory dict
of live `Subrun` objects — threads and completion events, which cannot be rebuilt — so a
resumed leg starts with an EMPTY registry while `runs/<ts>/sub/<n>/` still holds every child
the earlier leg started, one of them with a finished hand-back in `artifacts/`. The run was
then told "no sub-workflows spawned this run".

SALVAGE, NOT RECOVERY, deliberately. An orphaned child cannot be waited on, killed or
collected: its thread died with the leg that owned it. What it CAN do is say where the work is,
so the parent reads the hand-back off disk instead of re-spawning it. That is also why the
state is its own word (`orphaned-by-resume`) rather than a `status` value — every live status
describes a child the engine is still in charge of.

The directories are the record and they outlive the leg (`childrun._highest_child_number`
already relies on exactly that for numbering). So nothing new is written at spawn time: this
reads what is already on disk, which cannot drift out of sync with reality the way a
second bookkeeping file would.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .observations import truncate

STATE = "orphaned-by-resume"

# How the `-m` of a row's identity is recovered. A child's transcript opens with its own
# run_started event, which carries the label and workflow the parent passed — the only place
# they survive the leg (the live Subrun held them in memory).
_IDENTITY_KEYS = ("label", "workflow")


def _identity(child_dir: Path) -> dict:
    """The label/workflow a child recorded for itself, best-effort.

    A child killed before its first write leaves an empty or missing transcript — then the
    row still exists (the directory is the evidence that it ran) and simply carries no label,
    because inventing one would say more than the disk knows.
    """
    out = {"label": "", "workflow": ""}
    try:
        with (child_dir / "transcript.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                for key in _IDENTITY_KEYS:
                    if not out[key] and isinstance(ev.get(key), str):
                        out[key] = ev[key]
                if all(out.values()):
                    break
    except OSError:
        pass
    return out


def _artifacts(child_dir: Path) -> list[str]:
    """What the child handed back, by name — the reason salvage is worth anything."""
    try:
        return sorted(p.name for p in (child_dir / "artifacts").iterdir() if p.is_file())
    except OSError:
        return []


def orphan_rows(run_dir: Path, live: set[int]) -> list[dict]:
    """One row per `sub/<n>/` directory this leg has no live child for.

    `live` is the registry's keys: a running child HAS a directory too, and its live row is
    the better one (it carries the thread, the turn count and the summary), so the two are
    never both reported.
    """
    try:
        numbered = sorted((p for p in (run_dir / "sub").iterdir() if p.name.isdecimal()),
                          key=lambda p: int(p.name))
    except OSError:
        return []
    rows = []
    for d in numbered:
        n = int(d.name)
        if n in live:
            continue
        ident = _identity(d)
        rows.append({"n": n, "label": ident["label"], "workflow": ident["workflow"],
                     "mode": "", "state": STATE,
                     # No turn count and no elapsed: the counters lived in the Subrun object.
                     # A zero here is honest only because the row's state says it is unknown.
                     "turns": 0, "elapsed_s": 0.0, "summary_head": "",
                     "dir": str(d), "artifacts": _artifacts(d)})
    return rows


def rows(live: dict, run_dir: Path) -> list[dict]:
    """EVERY child this run has, in number order: the live ones from the registry and the
    orphans from disk.

    One function owns the row shape because there are now two sources for it, and a shape that
    lives in two modules drifts — the salvaged rows would quietly stop carrying a field the
    live ones gained, and the renderer would read `None` for it.

    `dir` and `artifacts` are ORPHAN-ONLY, and deliberately: a live child's hand-back arrives
    by itself (the engine copies it into the parent's `artifacts/from-sub-<n>/` on exit), so
    the path is what a parent needs exactly when the engine can no longer hand anything back.
    """
    now = time.monotonic()
    out = [{"n": sub.n, "label": sub.label, "workflow": sub.workflow, "mode": sub.mode,
            "state": sub.status if sub.done.is_set() else "running",
            "turns": sub.ctx.turn,
            # a finished child's elapsed is how long it RAN, not how long ago it started
            "elapsed_s": round((sub.ended_mono or now) - sub.started_mono, 1),
            "summary_head": truncate(sub.summary, cap=200)[0] if sub.done.is_set() else ""}
           for sub in live.values()]
    out += orphan_rows(run_dir, set(live))
    out.sort(key=lambda r: r["n"])
    return out


def no_such_child(run_dir: Path, n: int) -> str:
    """Why child `n` cannot be waited on or killed — ONE answer for both, because the two
    refusals differ only in a verb and a run acts on either the same way.

    A directory without a thread is the salvageable case and says so. "No sub-workflow N"
    reads as "it never existed", which sends the run off to re-spawn work already done — the
    reason this function looks at the disk before it answers.
    """
    if (run_dir / "sub" / str(n)).is_dir():
        return (f"sub-workflow {n} was started by an EARLIER LEG of this run, so this leg holds "
                f"no thread for it: it cannot be waited on or killed, and its exit will not be "
                f"announced. Its work is on disk at {run_dir / 'sub' / str(n)} — read its "
                f"artifacts/ and transcript.jsonl instead of spawning it again.")
    return (f"no sub-workflow {n} — the `subruns` action lists every child this run has "
            "spawned, with its number and state")
