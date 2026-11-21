"""THE reminder read: definitions from the hook stream, tallies from the routine's own store.

The reminder twin of `hooks/reportread`, and deliberately not a copy of it. The reports cutover
could repoint its read wholesale because a report row is entirely derivable from its hooks. A
reminder is not: `state/reminders.json` carries a per-reminder `stats` block (`fires`,
`could_not`, `would_have`, `did`, `didnt`) and a top-level `global_stats` tally about GLOBAL
reminders, and `hooks/store.record_reminder` deliberately does not mirror either — its own
docstring says mirroring a counter would give one number two writers. Measured on the live
instance 2026-11-15: **150 of 196 reminders carry a non-zero tally**, 12 global ids carry one.
A read that merely folded the stream would silently zero all of it, and that tally IS the
routine's accumulated evidence about whether a pattern earns its interruptions
(`remind_feedback` exists to write it).

So each field has ONE source:

- **definition** (regex, description, kind, scope, reach, created_run, owner, disposition) —
  the STREAM's, and only the stream's: what holds a run is derived from the one write path
  rather than read from a file any writer could have touched. A reminder the store names that
  the stream does not is not served; nothing compares the two stores on a read (the fallback
  that did, and filed `reminder_stream_*` when it served the store, was retired once the
  stores had measured equal for weeks).
- **tally** (and a mute's run stamp) — the STORE's, always, because the stream has none.

`load_local` stays the STORE's reader and is NOT repointed: the three raw mutation paths
(`record`, `dispose`, `set_for`) work off the file, and a mutator that read its own projection
would rewrite definitions from a served fold.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..reminders import Reminder, load_local
from .reminderproj import definitions as _definitions

log = logging.getLogger(__name__)

__all__ = ["active", "materialize_stream_only", "read_local", "stream_definitions"]


def active(routine_dir: Path, reminders_home: Path, level: str,
           listed: list[str] | tuple[str, ...] = (), run_id: str = "",
           *, routines_home: Path | None = None) -> list[Reminder]:
    """THE LIVE SET for one run, with its local definitions served from the hook stream.

    The union `reminders.active` has always formed — this routine's own reminders plus every
    curated one that reaches it (universal, or named in `listed`), local overriding global,
    deduped by `(regex, kind)` — with one change: the LOCAL half comes through `read_local`, so
    what HOLDS a run is derived from the one write path instead of read from a file any writer
    could have touched. The global half still comes from the library store, where a curated
    reminder lives, and the tallies still come from the routine's own store.

    This is the cutover's load-bearing step, and it is the LAST one for a reason: the health
    view (`readmodels/run_health`) is a page, while this decides whether an action is HELD. It
    is safe to take now because the three hazards that blocked it are closed — the console's
    delete reaches the stream, a holder's disposition no longer takes over foreign ownership,
    and a stream-only definition gets a real row.

    That last one is why `materialize_stream_only` runs HERE, before the read: the three raw
    mutation paths (`record`'s tally, `dispose`'s keep/mute/remove, `set_for`'s cap and
    duplicate checks) work off the store, so a definition the stream serves must exist there or
    a run could hold a reminder it cannot count, silence or cap. Doing it at the one point where
    the live set is formed means every fire point inherits it, and the mutators keep reading raw
    — never their own projection.

    `routines_home` defaults to `routine_dir.parent`, which is where a routine's home is; the
    engine passes the configured one explicitly, because a conversation's parent directory is
    not the server's hook home.
    """
    from .. import remind_dead, remind_mute
    from ..reminders import LEVEL_RANK, load_global

    if LEVEL_RANK.get(level, 0) < LEVEL_RANK["local"]:
        return []        # the layer is off: read nothing, materialize nothing
    home = routines_home if routines_home is not None else routine_dir.parent
    materialize_stream_only(home, routine_dir)
    stored, gstats = read_local(home, routine_dir)
    local = remind_dead.live(routine_dir, remind_mute.live_local(stored, run_id))  # D231, F697
    seen = {(r.regex, r.kind) for r in local}
    return local + [g for g in load_global(reminders_home, gstats)
                    if (g.regex, g.kind) not in seen
                    and (g.reach == "universal" or g.id in listed)]


def stream_definitions(routines_home: Path, routine: str) -> dict[str, dict]:
    """The definitions the hook stream implies for the reminders this routine HOLDS.

    Module-level rather than a function-local import so a test can substitute it: a hook stream
    in a particular state is otherwise only reachable by writing one.
    """
    return _definitions(routines_home, routine=routine)


def materialize_stream_only(routines_home: Path, routine_dir: Path) -> list[str]:
    """Give every STREAM-ONLY local definition a real row in the routine's own store, and
    return the ids that gained one. The cutover's third hazard, closed at the read seam.

    WHY THIS IS NEEDED. Once definitions are served from the stream, a reminder can be live
    for a run while having NO row in `state/reminders.json` — a recovery state the read
    deliberately allows. But the three raw mutation paths work off that file and silently do
    nothing for an id that is not in it:

    - `reminders.record` (the ONLY writer of a tally) returns the caller's stats unchanged when
      the id is gone from disk, so every fire of such a reminder goes uncounted — and the tally
      is exactly what tunes a pattern, and what `remind_feedback` exists to write;
    - `reminders.dispose` returns "" because `find` sees nothing, so the target's keep / mute /
      remove over a hook it HOLDS cannot be applied — a run could not silence a bad pattern;
    - `reminders.set_for`'s cap and duplicate checks count only stored rows, so a stream-only
      definition is invisible to both and the cap can be exceeded.

    WHY HERE AND NOT IN THOSE PATHS. Repointing a mutator at the merged read would make it
    read its own projection — `record` would rewrite definitions from a served fold, `dispose`
    would materialize wholesale as a side effect of feedback. So the stores' writers stay RAW
    and the gap is closed before they run: the row is created once, from the stream's own
    definition, with a BLANK tally because no fire has been counted for it (inventing one
    would be the two-writers defect this campaign removes).

    Local scope only — a global definition lives in the library store and is merged separately;
    copying one into a routine's private file would put it where no curation reaches it.
    Best-effort: a store that cannot be written leaves the ids unmaterialized and the run still
    starts.
    """
    from ..reminders import blank_stats, save_local

    try:
        stored, gstats = load_local(routine_dir)
        derived = stream_definitions(routines_home, routine_dir.name)
    except OSError:
        log.exception("reminder-hooks: materializing stream-only definitions raised")
        return []
    have = {r.id for r in stored}
    fresh = [(rid, d) for rid, d in derived.items()
             if rid not in have and str(d.get("scope") or "local") == "local"]
    if not fresh:
        return []
    rows = [*stored]
    for rid, d in fresh:
        rows.append(Reminder(
            id=rid, regex=str(d.get("regex") or ""),
            description=str(d.get("description") or ""),
            scope="local", kind=str(d.get("kind") or ""),
            reach=str(d.get("reach") or ""), owner=str(d.get("owner") or ""),
            created_run=str(d.get("created_run") or ""),
            disposition=str(d.get("disposition") or "keep"), stats=blank_stats()))
    try:
        save_local(routine_dir, rows, gstats)
    except OSError:
        log.exception("reminder-hooks: writing materialized stream-only definitions raised")
        return []
    return [rid for rid, _ in fresh]


def read_local(routines_home: Path, routine_dir: Path) -> tuple[list[Reminder], dict[str, dict]]:
    """This routine's live reminders (stream definitions + stored tallies) and its global tally.

    Shaped exactly like `reminders.load_local`, so a caller swapping to it changes nothing else.
    """
    stored, gstats = load_local(routine_dir)
    by_id = {r.id: r for r in stored}
    # LOCAL only, and this is a correctness boundary rather than tidiness. `load_local`'s
    # contract is "this routine's OWN reminders, plus its tally about global ones": a global
    # reminder lives in the shared library store and is merged in separately, local-over-global
    # by regex. The stream's `definitions(routine=…)` answers the wider question — every hook
    # this routine HOLDS, global ones included — so serving them here would put 11 library
    # reminders (measured on the live fleet: `rules-review` 10, `vodafone-outage-claims` 1) into
    # the list whose writers persist to `state/reminders.json`, duplicating a shared definition
    # into a routine's private file where no curation reaches it.
    local = {rid: d for rid, d in stream_definitions(routines_home, routine_dir.name).items()
             if str(d.get("scope") or "local") == "local"}

    out: list[Reminder] = []
    for rid, definition in local.items():
        held = by_id.get(rid)
        out.append(Reminder(
            id=rid,
            regex=str(definition.get("regex") or ""),
            description=str(definition.get("description") or ""),
            scope=str(definition.get("scope") or "local"),
            created_run=str(definition.get("created_run") or ""),
            # the one field the stream cannot carry — 150 of 196 live rows have one
            stats=(held.stats if held else _zero_stats()),
            reach=str(definition.get("reach") or ""),
            kind=str(definition.get("kind") or "action"),
            owner=str(definition.get("owner") or ""),
            muted_run=(held.muted_run if held else ""),
            disposition=str(definition.get("disposition") or ""),
        ))
    return out, gstats


def _zero_stats() -> dict:
    from ..reminders import STAT_FIELDS

    return dict.fromkeys(STAT_FIELDS, 0)

