"""WRITING a reminder down — the store half of the reminder layer, lifted out of `remind.py`.

`engine/remind.py` is the runtime: interception, the authoring ops, the ask-backs, the tally.
This module is the three functions that actually PUT a definition somewhere — the local file,
the library file and its commit — plus the hook mirror that records the same write in the one
hook store (`engine/hookmirror`, `rsched/hooks`, D152).

The boundary was already in the code: every op ends by calling `persist`, and nothing in these
three functions decides anything about an op. Separating them is what let the mirror land
without growing a file that is over the house size bar and may only shrink.

It is also where the two FOREIGN-store writes live, for that same reason and because they are
writes: `dispose`, a routine's say over a hook someone set on IT, and `curate` (D231), the
CURATOR's disposal of a row in a store it does not own — `dead` (the row keeps its tally and
stops firing, `rsched/remind_dead`) or `delete` for a row with no evidence worth keeping. That
second one exists because a reminder store's only other channel is an addressed report, read on
the owner's next scheduled run, which for a routine whose config is `enabled: false` never
comes; its authorization is the `reminders: global` dial that already governs writing the
shared store, asked once, with no second gate over it.
"""

from __future__ import annotations

from .. import reminders as store
from ..ids import now_iso
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


def remove(loop, target: Reminder) -> None:
    """DELETE one reminder of this routine's own — the live set, the store its scope names, and
    the hook row that records the deletion.

    Here beside `persist` because it is the same act in reverse, and because that is what was
    wrong: `persist` mirrored every write while the removal wrote only the store, so the stream
    kept definitions their routines had deleted. Measured over all 31 live routines on
    2026-11-14 (`hooks/reminderproj.drift`): **23 ids on the stream and in no store**, five of
    them from November. The campaign's end state is the store SERVED from the stream, and a fold
    that cannot see a deletion re-raises a pattern its routine removed — which, since a reminder
    decides whether an action is HELD, changes what binds a run rather than merely looking stale.

    The mirror covers the LOCAL scope, which is what a routine's own store holds and what the
    comparator (`reminderproj.drift`) compares. A global reminder lives in the library and its
    deletion is already recorded where that store's history is — the library git commit the
    caller makes — so a `self`-addressed row for it would claim one routine's view of a store
    every routine reads.
    """
    loop.reminders = [r for r in loop.reminders if r.id != target.id]
    if target.scope == "global":
        store.delete_global(loop.ctx.server.reminders_home, target.id)
        return
    save_local(loop)
    hookmirror.reminder_removed(loop, target)


def dispose(loop, target: Reminder, disposition: str) -> str:
    """Write the TARGET's disposition of a foreign hook — `keep`, `mute` or `remove` — and
    record it on the hook stream. Returns what was actually applied, or "" when the record is
    no longer in the store.

    Here rather than in `remind.py` because this is a WRITE, which is this module's whole
    subject: the store write and the hook row are one act, and a caller that could do the first
    without the second is how the two came to disagree in the first place (D152 step 4 — step 2
    recorded a reminder's definition and never what the target decided about it).
    """
    applied = store.dispose(loop.ctx.routine.dir, target.id, str(disposition),
                            loop.ctx.run_id)
    if applied:
        hookmirror.reminder_disposed(loop, target, applied)
    return applied


def curate(loop, op: dict, slug: str, rid: str, verb: str) -> str:
    """The CURATOR's disposal of one row in ANOTHER routine's store (D231) — the note the
    curating run reads back, never an exception: a refused disposal is not a failed turn.

    `dead` is the primary act and `delete` the fallback, which is the operator's own ordering:
    "prefer ANNOTATE-AS-DEAD over DELETE, so a row's hold tally survives", because "a deletion
    loses the tally that proves the pattern was too broad". So `dead` keeps the row and every
    count on it and stops it firing (`rsched/remind_dead`), while `delete` is for a row whose
    tally says nothing worth keeping.

    WHY THIS EXISTS AT ALL: a reminder store belongs to its routine, so the curator's only
    channel was an addressed report, read on the owner's NEXT SCHEDULED RUN — and a routine
    whose config is `enabled: false` has none. Four consecutive curation passes recorded the
    same four rows nobody could dispose of. `enabled` is therefore consulted nowhere here: the
    holder's schedule is the holder's business, and a capability that only worked on disabled
    routines would leave the curator unable to retire the row in the live routine next door.

    AUTHORIZATION is the dial that already exists and gets no second gate: `reminders: global`
    — the curator's setting, what lets a routine write the SHARED store — asked once through
    `GrantPolicy.reminder_denial`, so a routine without it is refused with the denial that
    names what is missing. A sub-workflow is refused like every other cross-routine write.
    """
    from pathlib import Path

    from .. import remind_dead

    ctx = loop.ctx
    act = "marked dead" if verb == "dead" else "deleted"
    if ctx.depth > 0:
        return (f"sub-workflows cannot have a row in another routine's store {act} — it "
                "changes a routine nobody in this tree supervises; say so in your summary")
    if (grants := ctx.grants) is not None and (denial := grants.reminder_denial("global")):
        return (f"{rid} is a row in {slug}'s own store, so nothing was {act} — curating "
                f"another routine's rows is the same grant as writing the shared store: "
                f"{denial}")
    if not slug:
        return (f"`op={verb}` names no `target`, so there is no store to curate — your own "
                "reminder is deleted with `op=delete` and no target")
    target_dir = Path(ctx.server.routines_home) / slug
    if not target_dir.is_dir():
        return f"no routine '{slug}' is installed — nothing was {act}"
    reason = str(op.get("description") or "")
    if verb == "delete":
        return _curate_delete(loop, target_dir, slug, rid, reason)
    marked = remind_dead.annotate(target_dir, rid, by=ctx.routine.slug, reason=reason,
                                  at=now_iso())
    if marked is None:
        return (f"{slug} does not hold a reminder {rid!r} — nothing was marked dead; the ids "
                "are the rows of that routine's own store")
    hookmirror.reminder_curated(loop, marked, slug, "dead")
    counts = " / ".join(f"{n} {f}" for f, n in (marked.get("stats") or {}).items() if n)
    return (f"{remind_dead.line(rid, marked[remind_dead.DEAD_KEY])} — it fires no more in "
            f"{slug}'s runs and its tally is kept ({counts or 'no fires recorded'})")


def _curate_delete(loop, target_dir, slug: str, rid: str, reason: str) -> str:
    """The fallback: remove a row outright, for one whose tally says nothing worth keeping.

    Here rather than in `curate` because it is the branch that DESTROYS evidence, and the note
    says so: a reader of the stream has to be able to tell the two acts apart.
    """
    from .. import remind_dead

    live, gstats = store.load_local(target_dir)
    if (row := store.find(live, rid)) is None:
        return (f"{slug} does not hold a reminder {rid!r} — nothing was deleted; the ids are "
                "the rows of that routine's own store")
    store.save_local(target_dir, [r for r in live if r.id != rid], gstats)
    remind_dead.forget(target_dir, rid)      # the row is gone; its annotation goes with it
    hookmirror.reminder_curated(loop, row.as_record(), slug, "remove")
    return (f"{rid} deleted from {slug}'s store ({reason or 'no reason given'}) — its tally "
            f"went with it; `op=dead` keeps a tally that is still evidence")


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
