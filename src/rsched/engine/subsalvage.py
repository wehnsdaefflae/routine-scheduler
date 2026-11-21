"""Children a LATER LEG cannot see in memory, salvaged from the directories they left.

`SubrunManager.subruns` holds live threads, which cannot be rebuilt, so a resumed leg starts
with an EMPTY registry while `runs/<ts>/sub/<n>/` still holds every child the earlier leg
started (F732).

SALVAGE, NOT RECOVERY: an orphan cannot be waited on, killed or collected, but it can say
where its work is so the parent reads it off disk instead of re-spawning it. Its state is its
own word (`orphaned-by-resume`), not a live `status`.

The directories are the record (numbering relies on them too), so nothing extra is written at
spawn time and nothing can drift.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .observations import truncate

STATE = "orphaned-by-resume"

# A child's transcript records the label and workflow the parent passed — the only place they
# survive the leg.
_IDENTITY_KEYS = ("label", "workflow")


def _identity(child_dir: Path) -> dict:
    """The label/workflow a child recorded for itself, best-effort.

    A child killed before its first write still gets a row, just with no label.
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

    `live` is the registry's keys: a running child has a directory too, and its live row wins.
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
                     # counters lived in the Subrun; the state says these zeros are unknown
                     "turns": 0, "elapsed_s": 0.0, "summary_head": "",
                     "dir": str(d), "artifacts": _artifacts(d)})
    return rows


def rows(live: dict, run_dir: Path) -> list[dict]:
    """EVERY child this run has, in number order: the live ones from the registry and the
    orphans from disk.

    One function owns the row shape for both sources so they cannot drift. `dir` and
    `artifacts` are ORPHAN-ONLY: a live child's hand-back is copied to the parent on exit.
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
    """Why child `n` cannot be waited on or killed — ONE answer for both.

    A directory without a thread is the salvageable case and says so, rather than reading as
    "it never existed" and sending the run to re-spawn finished work.
    """
    if (run_dir / "sub" / str(n)).is_dir():
        return (f"sub-workflow {n} was started by an EARLIER LEG of this run, so this leg holds "
                f"no thread for it: it cannot be waited on or killed, and its exit will not be "
                f"announced. Its work is on disk at {run_dir / 'sub' / str(n)} — read its "
                f"artifacts/ and transcript.jsonl instead of spawning it again.")
    return (f"no sub-workflow {n} — the `children` action lists every child this run has "
            "started, with its number and state")


def turns_reached(child_dir: Path) -> int:
    """The highest turn the child's OWN transcript recorded — how much work died with it.

    Zero means no turn stamp was recorded, never that the child did nothing.
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


def spend_reached(child_dir: Path) -> dict:
    """What a DEAD child actually spent, read from its OWN transcript — `{}` when nothing is
    recorded there.

    An orphan is never COLLECTED (`subruns._collect`), so its spend exists only in its own
    transcript (R3112). What the disk records, never an estimate, summed exactly as
    `history.prior_usage` sums a leg's so both mean the same thing.
    """
    from ..endpoints.usage import fold_usage

    total: dict = {}
    try:
        with (child_dir / "transcript.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                etype = ev.get("type")
                if etype == "assistant_action":
                    usage = ev.get("usage")
                elif ((etype == "observation"
                       and (ev.get("payload") or {}).get("kind") in ("llm", "decide"))
                      or etype in ("compaction", "child_end")):
                    usage = (ev.get("payload") or {}).get("usage")
                else:
                    continue
                if isinstance(usage, dict) and usage:
                    fold_usage(total, usage)
    except OSError:
        return {}
    return total


def _orphan_line(run_dir: Path, orphan: dict) -> str:
    """One dead child, as the resumed leg reads it: what it was, how far it got, where its
    work is — and whether its original brief is still on disk to carry forward.

    The brief clause appears only when `instruction.md` exists — never promise a re-brief the
    disk cannot support.
    """
    d = run_dir / "sub" / str(orphan["n"])
    landed = ", ".join(_artifacts(d)) or "nothing in artifacts/"
    line = (f"- #{orphan['n']} {str(orphan.get('label') or '')!r} "
            f"({orphan.get('mode') or 'child'}, workflow {orphan.get('workflow') or '?'}) "
            f"reached turn {turns_reached(d)}; its work is at {d} (hand-back: {landed})")
    brief = d / "instruction.md"
    if brief.is_file():
        line += (f"\n  its ORIGINAL BRIEF survived at {brief} — read it and a fresh child can "
                 "carry it on from where this one stopped")
    return line


def resume_note(run_dir: Path, orphans: list[dict]) -> str:
    """What a RESUMED leg is told about the children that died with its earlier leg (F778).

    A child is a thread in the parent's process, so RE-ADOPTION is impossible
    (docs/child-runs.md § Process model). The two real options are READ what it left or
    RE-BRIEF a fresh child with the original brief plus that work; re-issuing the bare brief
    throws the work away, so the note distinguishes them.
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
