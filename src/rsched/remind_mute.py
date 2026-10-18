"""Is a muted hook still muted? — the ONE rule, so `mute` lasts exactly as long as it says.

F697: `mute` is documented in six places as silencing a foreign hook "for the rest of THIS
run", but `dispose` wrote `disposition="mute"` to the store and `reminders.active` filtered
every muted record out of the live set forever. A muted hook therefore never fired again — and
because a disposition is only ever an answer to a fire, it could never be offered the `keep`
that would undo it. A permanent silence already has a name (`remove`, which also records the
refusal so the owner cannot re-set the pattern); `mute` is the reversible one and had become
the irreversible one.

The fix is a STAMP rather than a reset at run end: the run that muted the hook is written on
the record, and a mute is live only while that same run is. Nothing has to clean up after a
run, so a crashed or killed run cannot leave a hook silenced for good.

Two readings matter, and they are both deliberate:

- a mute stamped with ANOTHER run's id is EXPIRED — the hook is live again, fires, and its
  holder may mute it again, keep it, or remove it;
- a mute with NO stamp is also expired. Those are the records written before this module
  existed, i.e. exactly the ones the finding is about: permanent silences nobody chose. They
  come back as live hooks, which is the documented behaviour they should have had. The
  alternative — reading an unstamped mute as "muted forever" — would preserve the defect in
  every store that already has one.

`run_id` is never empty for a real run; the default exists so a reader outside a run (a
console read model, a test helper) sees the stored dispositions rather than a run's view of
them, and in that case no mute is live.
"""

#: The disposition that is run-scoped. `keep` and `remove` are not: `keep` leaves the hook
#: live, `remove` deletes the record and writes the refusal ledger.
MUTE = "mute"


def is_muted(disposition: str, muted_run: str, run_id: str) -> bool:
    """Is this record's mute still in force for the run asking?

    Takes the three fields rather than a `Reminder` so the store can call it while it is still
    building one, and so the rule can be read and tested without constructing a record.
    """
    return bool(run_id) and disposition == MUTE and muted_run == run_id


def live_local(stored: list, run_id: str) -> list:
    """The stored records that are LIVE for this run — every one whose mute has expired.

    A hook the holder muted in THIS run fires nothing for the rest of it: the definition stays
    in the store, where the routine that set it can still see it (`reminders.dispose`), but it
    leaves the live set. A mute stamped with an earlier run, or with none at all, has expired.
    """
    return [r for r in stored if not is_muted(r.disposition, r.muted_run, run_id)]


def disposed(record: dict, disposition: str, run_id: str) -> dict:
    """That record with the target's decision applied, stamp and all."""
    return {**record, "disposition": disposition, "muted_run": stamp_for(disposition, run_id)}


def stamp_for(disposition: str, run_id: str) -> str:
    """The `muted_run` value to store for a disposition the target just applied.

    Only a mute carries a stamp: `keep` clears it, so a hook muted earlier in the same run and
    then kept is live again — the holder's last word in the run is the one that counts.
    """
    return run_id if disposition == MUTE else ""
