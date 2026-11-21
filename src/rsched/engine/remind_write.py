"""WRITING a reminder down — the store half of the reminder layer.

`engine/remind.py` is the runtime (interception, authoring ops, ask-backs, tally); this module
PUTS a definition somewhere — the local file, the library file and its commit — and mirrors each
write into the one hook store (`engine/hookmirror`, D152). It decides nothing about an op.

It also holds the two FOREIGN-store writes: `dispose`, a routine's say over a hook set on IT,
and `curate` (D231), the curator's disposal of a row in a store it does not own.
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

    The inverse of `persist`, and mirrored like it: a stream that cannot see a deletion
    re-raises a pattern its routine removed, which changes what a run is HELD on. Only the LOCAL
    scope is mirrored; a global reminder's deletion is recorded by the library commit the caller
    makes, and a `self`-addressed row would claim one routine's view of a shared store.
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

    The store write and the hook row are one act, so no caller can do the first alone.
    """
    applied = store.dispose(loop.ctx.routine.dir, target.id, str(disposition),
                            loop.ctx.run_id)
    if applied:
        hookmirror.reminder_disposed(loop, target, applied)
    return applied


def curate(loop, op: dict, slug: str, rid: str, verb: str) -> str:
    """The CURATOR's disposal of one row in ANOTHER routine's store (D231) — the note the
    curating run reads back, never an exception: a refused disposal is not a failed turn.

    `dead` is preferred: it keeps the row and its tally (the evidence a pattern was too broad)
    and stops it firing (`rsched/remind_dead`); `delete` is for a row whose tally says nothing.

    It exists because an addressed report is read only on the owner's next scheduled run, which
    a disabled routine never has; `enabled` is consulted nowhere here. AUTHORIZATION is the
    existing `reminders: global` dial via `GrantPolicy.reminder_denial`, with no second gate. A
    sub-workflow is refused like every other cross-routine write.
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
    # A row retired from THIS run's own store leaves the live set too: the dead filter runs
    # only when the live set is formed, so the cached row would otherwise keep holding actions.
    if slug == ctx.routine.slug:
        loop.reminders = [r for r in loop.reminders if r.id != rid]
    counts = " / ".join(f"{n} {f}" for f, n in (marked.get("stats") or {}).items() if n)
    return (f"{remind_dead.line(rid, marked[remind_dead.DEAD_KEY])} — it fires no more in "
            f"{slug}'s runs and its tally is kept ({counts or 'no fires recorded'})")


def _curate_delete(loop, target_dir, slug: str, rid: str, reason: str) -> str:
    """The fallback: remove a row outright, for one whose tally says nothing worth keeping.

    A separate branch because it DESTROYS evidence, and the note says so.
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

    `store.record` is the only tally writer, so DISK owns stats; this run's ops change the
    definitions, so MEMORY owns those (an in-memory `Reminder` never sees the increments).
    MUTED records are kept from disk: mute drops a foreign hook from the live set for this run
    without destroying the owner's definition (`remind_mute`).
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
