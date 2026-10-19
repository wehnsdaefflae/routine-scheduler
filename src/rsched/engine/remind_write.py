"""WRITING a reminder down — the store half of the reminder layer, lifted out of `remind.py`.

`engine/remind.py` is the runtime: interception, the authoring ops, the ask-backs, the tally.
This module is the three functions that actually PUT a definition somewhere — the local file,
the library file and its commit — plus the hook mirror that records the same write in the one
hook store (`engine/hookmirror`, `rsched/hooks`, D152).

The boundary was already in the code: every op ends by calling `persist`, and nothing in these
three functions decides anything about an op. Separating them is what let the mirror land
without growing a file that is over the house size bar and may only shrink.
"""

from __future__ import annotations

from .. import reminders as store
from ..reminders import Reminder
from . import hookmirror


def persist(loop, reminder: Reminder, message: str) -> None:
    """Write ONE reminder to the store its scope names — the local file (rewritten whole, it is
    small and single-writer) or its own file in the library, committed like any library write.

    Then mirror it as a hook: a reminder is `trigger: match` over an action, a result, the
    turn's prose or a turn moment, delivered as a hold (the `action` kind) or at the turn
    boundary (every other). Best-effort, and after the real write.
    """
    if reminder.scope == "global":
        home = loop.ctx.server.reminders_home
        home.mkdir(parents=True, exist_ok=True)
        store.write_global(home, reminder)
        commit(loop, message, reminder)
    else:
        save_local(loop)
    hookmirror.reminder_written(loop, reminder)


def save_local(loop) -> None:
    """Rewrite the local store: DEFINITIONS from memory, TALLIES from disk.

    The split is load-bearing. `store.record` is the only writer of a tally and does its own
    read-modify-write, so DISK is authoritative for stats; this run's ops are what changed the
    definitions, so MEMORY is authoritative for those. Taking both halves from memory rolled
    every `fires` this run recorded back to its boot-time value, because a frozen in-memory
    `Reminder` never saw the increment — the global tallies were already merged this way, and
    the local half needed the same treatment.

    MUTED records are the one exception to "memory owns the definitions", and for the reason
    mute exists: it silences a foreign hook for the rest of this run WITHOUT destroying the
    definition its owner can still see, so the live set no longer holds it — and a rewrite from
    memory alone would delete it — though its `muted_run` stamp expires (`remind_mute`, F697).
    """
    on_disk, gstats = store.load_local(loop.ctx.routine.dir)
    tallies = {r.id: r.stats for r in on_disk}
    live_ids = {r.id for r in loop.reminders}
    merged = [Reminder(**{**r.as_record(), "stats": tallies[r.id]}) if r.id in tallies else r
              for r in loop.reminders]
    merged += [r for r in on_disk if r.disposition == "mute" and r.id not in live_ids]
    store.save_local(loop.ctx.routine.dir, merged, gstats)


def commit(loop, message: str, reminder: Reminder) -> None:
    """Commit a global reminder's file in the library, like any other library write."""
    from .. import libgit

    libgit.commit(loop.ctx.server.libraries_home, message,
                  routines_home=loop.ctx.server.routines_home,
                  paths=[store.global_rel(reminder.id)])
