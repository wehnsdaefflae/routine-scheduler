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


def _turns_reached(child_dir: Path) -> int:
    """The highest turn the child's OWN transcript recorded — how much work died with it.

    Zero means the transcript carries no turn stamp (a child killed before its first), never
    that the child did nothing: the note says what the number is, and inventing one would
    claim more than the disk knows.
    """
    top = 0
    try:
        with (child_dir / "transcript.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev.get("turn"), int):
                    top = max(top, ev["turn"])
    except OSError:
        pass
    return top


def _orphan_line(run_dir: Path, orphan: dict) -> str:
    """One dead child, as the resumed leg reads it: what it was, how far it got, where its
    work is — and whether its original brief is still on disk to carry forward.

    The brief clause is CONDITIONAL on the file being there, because a re-brief the note
    promises and the disk cannot support is worse than no offer: `childrun.materialize_to_disk`
    writes `instruction.md` for every child, so its absence means this child never got that far.
    """
    d = run_dir / "sub" / str(orphan["n"])
    landed = ", ".join(_artifacts(d)) or "nothing in artifacts/"
    line = (f"- #{orphan['n']} {str(orphan.get('label') or '')!r} "
            f"({orphan.get('mode') or 'child'}, workflow {orphan.get('workflow') or '?'}) "
            f"reached turn {_turns_reached(d)}; its work is at {d} (hand-back: {landed})")
    brief = d / "instruction.md"
    if brief.is_file():
        line += (f"\n  its ORIGINAL BRIEF survived at {brief} — read it and a fresh child can "
                 "carry it on from where this one stopped")
    return line


def resume_note(run_dir: Path, orphans: list[dict]) -> str:
    """What a RESUMED leg is told about the children that died with its earlier leg (F778).

    The note this replaces said the opposite of the truth — "did NOT survive the restart
    (results lost) … Re-issue any you still need" — while every child's work sat on disk under
    `sub/<n>/`. Measured cost, 2026-10-25: `scheduler-builder`'s run died at turn ~285 with two
    live children holding ~1,400 s of real build work each, and the resumed parent had to find
    that work by hand. A salvage nobody is told about is not survivability, which is why this
    wording is part of the fix rather than a nicety on top of it.

    RE-ADOPTION IS NOT ON THE LIST, and that is a fact about this engine rather than a gap
    here: a child is an EngineLoop on a daemon THREAD inside the parent's process
    (`subruns._start`), so it dies with the leg that owned it and there is no process to pick
    back up. `docs/child-runs.md` § Process model records that decision and names
    "reattach-on-resume" among the costs deliberately not paid. So the two real options are
    READ what the dead child left, or RE-BRIEF a fresh one with the original brief plus that
    work — and the note has to distinguish them, because re-issuing a bare copy of the brief is
    what throws the 1,400 s away.
    """
    if not orphans:
        return ""
    return ("these CHILD RUNS were running when this run was interrupted, and they died with "
            "the leg that owned them — a child is an EngineLoop on a daemon THREAD inside this "
            "process, so there is no thread left to wait on, to kill, or to RE-ADOPT. What "
            "they produced is NOT gone: it is on disk, and these are the paths.\n"
            + "\n".join(_orphan_line(run_dir, o) for o in orphans)
            + "\n  So, per child: READ what it left (its artifacts/ and transcript.jsonl above "
              "— the directory IS the deliverable), or RE-BRIEF it — start a fresh child whose "
              "prompt carries the original brief above PLUS what the dead one already produced, "
              "so the work is continued instead of redone. Re-issuing the bare brief discards "
              "that work: do it only where the child left nothing behind.")
