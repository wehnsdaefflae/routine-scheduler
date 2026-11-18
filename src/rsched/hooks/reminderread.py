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

So the rule is a MERGE with a precedence per field:

- **definition** (regex, description, kind, scope, reach, created_run, owner, disposition) —
  the STREAM's, which is the point of the cutover: what holds a run is derived from the one
  write path rather than read from a file any writer could have touched.
- **tally** — the STORE's, always, because the stream has none.
- **a reminder the store names that the stream's fold does not** — served from the STORE with
  the shortfall filed, `reportread.folded`'s rule. Losing a report un-owes a reply; losing a
  reminder changes what HOLDS a run, so the fallback matters more here, not less.

`load_local` stays the STORE's reader and is NOT repointed. That is not tidiness: with
`reminderproj.stored_definitions` building the store side of the drift comparison out of it,
repointing it would make the comparator compare the stream to itself — `drift` would read EMPTY
however far apart the stores were, destroying the instrument goal b9 was measured with.

Precondition, now met: `reminderproj.drift` reads empty over all 31 live routines as of 0.549.0
(0 drifting ids, down from 122 through three one-shot migrations), so the two sides agree on
definitions before either serves.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..health_events import log_health_event
from ..reminders import Reminder, load_local
from .reminderproj import definitions as _definitions

log = logging.getLogger(__name__)

__all__ = ["read_local", "stream_definitions"]


def stream_definitions(routines_home: Path, routine: str) -> dict[str, dict]:
    """The definitions the hook stream implies for the reminders this routine HOLDS.

    Module-level rather than a function-local import so a test can substitute it: a hook stream
    in a particular state is otherwise only reachable by writing one, and the fallback paths
    below are exactly the states a fixture cannot easily produce.
    """
    return _definitions(routines_home, routine=routine)


def read_local(routines_home: Path, routine_dir: Path) -> tuple[list[Reminder], dict[str, dict]]:
    """This routine's live reminders (stream definitions + stored tallies) and its global tally.

    Shaped exactly like `reminders.load_local`, so a caller swapping to it changes nothing else.
    """
    stored, gstats = load_local(routine_dir)
    by_id = {r.id: r for r in stored}
    try:
        derived = stream_definitions(routines_home, routine_dir.name)
    except OSError:
        # A diagnostic is not worth a run that cannot start: `engine/remind.load` reads this
        # while the run is being constructed, which is why `load_local` is lenient too.
        log.exception("reminder-hooks: reading the hook stream for the reminder read raised")
        _fell_short(routines_home, routine_dir, sorted(by_id), served=len(stored), from_stream=0)
        return stored, gstats

    # LOCAL only, and this is a correctness boundary rather than tidiness. `load_local`'s
    # contract is "this routine's OWN reminders, plus its tally about global ones": a global
    # reminder lives in the shared library store and is merged in separately, local-over-global
    # by regex. The stream's `definitions(routine=…)` answers the wider question — every hook
    # this routine HOLDS, global ones included — so serving them here would put 11 library
    # reminders (measured on the live fleet: `rules-review` 10, `vodafone-outage-claims` 1) into
    # the list whose writers persist to `state/reminders.json`, duplicating a shared definition
    # into a routine's private file where no curation reaches it.
    local = {rid: d for rid, d in derived.items()
             if str(d.get("scope") or "local") == "local"}

    missing = [rid for rid in by_id if rid not in local]
    if missing:
        _fell_short(routines_home, routine_dir, missing, served=len(stored),
                    from_stream=len(local))
        return stored, gstats

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


def _fell_short(routines_home: Path, routine_dir: Path, missing: list[str], *, served: int,
                from_stream: int) -> None:
    """File the event that says the read did NOT come from the stream — the one place for it.

    Two names because the repair differs, exactly as the reports half names them apart:
    `reminder_stream_unreadable` when the stream implies no reminder for this routine at all
    (it was lost, the backfill never ran, or this routine is fresh), and
    `reminder_stream_diverged` when it implies some but not all — a definition that reached the
    store without going through the one write path, which is precisely what step 4 removes.
    """
    gone = from_stream == 0
    event = "reminder_stream_unreadable" if gone else "reminder_stream_diverged"
    detail = (f"the reminder read served state/reminders.json ({served} reminders) instead of "
              f"the hook stream, which "
              + ("implies none at all for this routine" if gone
                 else f"implies {from_stream} and is missing {len(missing)}: "
                      f"{', '.join(missing[:12])}")
              + ". Since D152 step 4 the stream is what a run's reminders are derived from and "
                "the store keeps the tallies, so this is a shortfall in the stream, not in the "
                "store — and a lost definition changes what HOLDS a run.")
    try:
        log_health_event(routines_home, event, routine=routine_dir.name, run_id="",
                         detail=detail, missing=len(missing), served=served,
                         from_stream=from_stream)
    # Broad on purpose: a diagnostic must never be the reason a run fails to start.
    except Exception:
        log.exception("reminder-hooks: filing the read shortfall raised")
