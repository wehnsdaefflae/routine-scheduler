"""Which blocked-work events are blocks ONLY THE OPERATOR CAN CLEAR (F796).

`health_stream.BLOCKED_EVENTS` answers "what did the fleet owe and not deliver". It does not
say WHO can do something about it, and that distinction decides which surface a row belongs
on: a block a run will clear by itself belongs on a panel someone reads when they wonder how
the fleet is doing, and a block that waits for a person belongs where that person cannot miss
it. Before this, both reached exactly one place — the Stats page's "Blocked work" panel,
inside a 7-day window, counted on no badge — so `backup_stale` ran nineteen unnoticed nights
and a pause that ate eight due fires was found by an audit reading the absence of run
directories.

So the set here is deliberately SMALL, and what it excludes is the half worth reading. The
test is not "is this bad" and not "is this the operator's fault" — it is: **can any run,
watcher or cooldown clear this, or does it wait for his hand on the host or on a switch only
he holds?**

IN — nothing inside the system can clear these:
  - `backup_stale` — the host's `rsched-backup.service` did not complete. No run can start
    it, and the instance keeps running unbacked until he looks at the unit.
  - `fire_paused` / `lane_fire_paused` — scheduling is paused and due fires are being
    dropped, never made up (D237). Only he unpauses, and every cron interval that passes
    costs another fire.
  - `oneshot_skipped` — a one-shot came due while scheduling was paused or the daemon was
    down and was consumed UNFIRED. Nothing makes it up, so only he can decide to re-arm it.
  - `lane_chain_member_skipped` — a lane names a member that is not a routine in any home.
    That is a standing misconfiguration of the lane, so it recurs at every fire until the
    membership is corrected, and lane membership is his.

OUT, with the reason, because a badge that cries wolf is worse than no badge:
  - `fire_refused`, `scheduler_tick_error` — the next fire or the next tick clears them.
  - `trigger_capped` — the event's own sentence says the routine is dark "until the cap
    resets": the cap resets daily by itself.
  - `model_chain_exhausted` — a cooling member clears when the cooldown lapses, and the turn
    that could not be taken leaves a failed run page of its own.
  - `commit_failed` — the files are on disk and a later commit takes them.
  - `migration_expired`, `migration_due_soon` — real, and hard: no release ships until the
    converged migration code is deleted. But DELETING it is a builder's job, not his.
  - `item_dropped_live`, `obligation_overdue` — work owed by a routine; its own next run is
    what clears them.
  - `finish_gate_failed`, `wrong_turn_detected`, `process_overhead_high` — measurements of
    runs that happened, and the loudest events on the stream (202 `wrong_turn_detected` in
    three weeks on this instance). A badge counting those would never be dark, which is the
    same as not having one.

The count is DISTINCT ROWS, never occurrences: a row is one (event, subject) pair — one
thing to go and fix — so a pause that ate eight fires of one routine reads as one block, and
two routines' dropped fires read as two. That is the number a badge can carry.

This module holds no fold of its own: `health_stream.blocked_fleet` already parses, windows
and folds the stream (memoized on its fingerprint), and a second parser of the same file is
how two readers of one source drift. `annotate` takes that payload and marks it, the way
`vocabulary` lets the console render a label it was not compiled with — so the JUDGEMENT above
is served, never duplicated in JS.
"""

from __future__ import annotations

from .health_stream import BLOCKED_EVENTS

#: event -> what only the operator can do about it, in the imperative, for the row's own
#: "waiting on you" line. Every key is a `BLOCKED_EVENTS` name (pinned by a test): a name that
#: drifted out of that set would silently stop being counted.
OPERATOR_CLEARABLE: dict[str, str] = {
    "backup_stale": "the instance is running unbacked until rsched-backup.service completes — "
                    "read it with `journalctl --user -u rsched-backup`",
    "fire_paused": "scheduling is paused and this routine's due fires are being dropped, not "
                   "deferred — resume scheduling, or accept the loss deliberately",
    "lane_fire_paused": "scheduling is paused and this lane's due fires are being dropped, "
                        "not deferred — resume scheduling, or accept the loss deliberately",
    "oneshot_skipped": "a one-shot was consumed unfired and nothing makes it up (D237) — "
                       "re-arm it if that work is still wanted",
    "lane_chain_member_skipped": "a lane names a member that is not a routine in any home, so "
                                 "every fire of that chain skips it — correct the membership",
}


def is_operator_block(event: str) -> bool:
    """Whether this blocked-work event is one nothing inside the system will clear."""
    return event in OPERATOR_CLEARABLE


def annotate(payload: dict) -> dict:
    """`blocked_fleet`'s payload with the operator's share marked, as a fresh dict.

    Each row gains `operator` (bool) and, when true, `waiting_on_you` — the sentence naming
    what only he can do. The payload gains `operator_total`: how many DISTINCT rows those
    are, which is the badge's number. A corrected row is already gone from `rows` by the time
    this sees them (`health_stream._resolve`), so nothing here re-judges a correction.
    """
    rows = []
    for row in payload.get("rows") or []:
        event = str(row.get("event") or "")
        marked = dict(row)
        marked["operator"] = is_operator_block(event)
        if marked["operator"]:
            marked["waiting_on_you"] = OPERATOR_CLEARABLE[event]
        rows.append(marked)
    out = dict(payload)
    out["rows"] = rows
    out["operator_total"] = sum(1 for row in rows if row["operator"])
    out["operator_vocabulary"] = dict(OPERATOR_CLEARABLE)
    return out


def unknown_names() -> set[str]:
    """Any name here that `BLOCKED_EVENTS` does not carry — empty is the correct reading.

    The fold keeps only `BLOCKED_EVENTS`, so a name that drifts out of it can never appear in
    a row and this module would quietly count zero of them forever.
    """
    return set(OPERATOR_CLEARABLE) - set(BLOCKED_EVENTS)
