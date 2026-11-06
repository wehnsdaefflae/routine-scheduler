"""A rule assist's TALLY — `state/assists.json`, in the stat fields a reminder's tally keeps.

`rsched/assists.py` says what an assist IS and where its tally lives; this module is the
tally's whole read/write surface, and the one writer of the file (`assists.record_fire`
delegates here, so a fire and a label cannot disagree about the shape on disk).

It was an INT per key — how often each assist fired — and that answered the only question the
free rungs raise: which triggers fire, and how often. The `hold` rung spends a turn, and a
count cannot say whether that turn bought anything. The one `hold`-payload assist in the
library stood at 31 / 31 / 10 fires across three routines — 72 turns, with no way to tell a
useful hold from a false positive, which is the one number that decides whether a trigger keeps
a turn-costing rung (D239).

So the tally grows to the SAME fields a reminder's does — `fires` plus the four-way outcome
label — and deliberately not to a vocabulary of its own: the read model, the console's table
and the prune check all read one set of names (`reminders.STAT_FIELDS`), and a second spelling
of `could_not` would mean every reader learning both.

Lenient on READ, strict on WRITE. The legacy int is read as `{"fires": n}` and a value a hand
edit broke as zeros, because this rides the routine page's health payload and a routine whose
store is old or hand-broken is a routine with nothing to show, not a broken page — the
one-shot migration (`migrate_assist_stats.py`) converts the file at the next daemon boot, and
the page may well be read before it. Every write leaves every field present.
"""

from __future__ import annotations

from pathlib import Path

from .assists import state_path
from .paths import atomic_write_json, read_json
from .reminders import STAT_FIELDS


def blank() -> dict:
    """One assist's tally before anything happened to it."""
    return dict.fromkeys(STAT_FIELDS, 0)


def normalize(raw: object) -> dict:
    """One key's stored value as a full tally — whatever is actually on disk.

    Three shapes reach this: the stat mapping a live engine writes, the INT the file carried
    before D239 (the fire count, kept as `fires`), and whatever a hand edit left (zeros rather
    than an exception — `int("lots")` used to raise and take the whole health payload down).
    """
    stats = blank()
    if isinstance(raw, dict):
        for field in STAT_FIELDS:
            try:
                stats[field] = int(raw.get(field) or 0)
            except (TypeError, ValueError):
                stats[field] = 0
        return stats
    try:
        stats["fires"] = int(raw or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        stats["fires"] = 0
    return stats


def read(routine_dir: Path) -> dict[str, dict]:
    """Every key in this routine's tally, each with all its stat fields.

    A missing or unreadable file reads as an empty tally: a routine no assist has ever fired in
    has no file at all, and that is not a failure of anything.
    """
    raw = read_json(state_path(routine_dir), {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): normalize(value) for key, value in raw.items()}


def write(routine_dir: Path, tallies: dict[str, dict]) -> None:
    """Replace the file with `tallies`, key-sorted. Best-effort, like every tally write: a
    failed tally must never fail the turn that produced it.
    """
    out = {key: normalize(value) for key, value in tallies.items()}
    try:
        path = state_path(routine_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, dict(sorted(out.items())))
    except (OSError, ValueError):
        pass


def record(routine_dir: Path, key: str, field: str) -> dict:
    """Increment one field of one key's tally and return the tally AFTER it.

    Off DISK, not off a caller's copy, exactly as `reminders.record` is: one routine is one
    writer and a run takes at most one turn at a time, so a read-modify-write is enough. A
    field that is not a stat field changes nothing — the caller's vocabulary is not the
    store's.
    """
    if field not in STAT_FIELDS:
        return read(routine_dir).get(key) or blank()
    tallies = read(routine_dir)
    stats = {**(tallies.get(key) or blank())}
    stats[field] = int(stats.get(field) or 0) + 1
    tallies[key] = stats
    write(routine_dir, tallies)
    return stats


def summary(stats: dict) -> str:
    """`31 fires: 2 could_not / 7 would_have` — the labels that have a count, for a note the
    run reads back. Mirrors the reminder layer's own wording so the two read alike.
    """
    counts = " / ".join(f"{n} {f}" for f in STAT_FIELDS[1:] if (n := int(stats.get(f) or 0)))
    return f"{int(stats.get('fires') or 0)} fires: {counts or 'none labelled'}"
