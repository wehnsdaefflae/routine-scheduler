"""Which blocked-work events are STANDING CONDITIONS and which are INCIDENTS (R3418).

`health_stream.BLOCKED_EVENTS` answers "what did the fleet owe and not deliver". It does not
say in WHAT UNIT a row's magnitude is, and until this split the fold assumed one unit for all
of them: a `count` of rows, summed into a single `total`.

That is right for an INCIDENT. A fire refused twice is two refusals, a chain stopped twice is
two chains — which is exactly why `health_cooldown` may never suppress one of these
(`REPEAT_COOLDOWN_HOURS`, pinned by a test): a suppressed row would make the fleet look less
stuck than it is.

It is wrong for a STANDING CONDITION, whose writer latches privately to one row a day while
ONE fact stays true. There the row count is the number of days THE DAEMON HAPPENED TO BE UP —
it grows with uptime, not with severity, so an instance that restarts often reports a smaller
problem. A nineteen-night backup gap rendered as `count: 19` beside `fire_refused: 19`, which
is nineteen distinct refusals, and both were summed into one number answering "what did the
fleet owe and not deliver". `migration_due_soon` sharpens it: by its own docstring it is
"deliberately filed while there is still room to act", so each of its rows said that nothing
had gone wrong yet.

So the magnitude of a standing row is its SPAN — how long the condition has been standing —
and the fold reports the two units separately, never added (`health_stream.blocked_fleet`).

WHAT MAKES AN EVENT STANDING is its writer's latch, not how bad it sounds:

  - `backup_stale` — `daemon/backup_watch`, "filed AT MOST ONCE A DAY while the condition
    holds (`_last_filed`)".
  - `migration_due_soon` / `migration_expired` — `daemon/marker_watch`, "the cadence is ONE
    row a day while the condition holds", latched on the watcher object.
  - `obligation_overdue` / `answer_undelivered` — `daemon/owed_watch`, one row per routine
    (or conversation) per day while the item stays unmet.
  - `process_overhead_high` — `engine/overhead_alarm.LATCH_HOURS` = 24, read back off the
    stream so a restart remembers it.

And what deliberately is NOT, though it looks it: `trigger_capped` is "one event per day AT
THE TRANSITION" (F276) — a cap re-earned by real trigger traffic is a new fact each time;
`scheduler_tick_error` is filed once per failure EPISODE, "until it next succeeds"
(`daemon/tickguard`); `postdeploy_failed` is once per PROCESS, and each boot that served the
wrong build is its own fact. Each of those three counts occurrences, which is what counting
them means.

This module holds the judgement alone, the way `operator_blocks` holds "who can clear it": one
place decides it, `health_stream` renders it into every row as `kind`, and the console branches
on that rather than being compiled with a list of event names. `health_stream` is imported
LAZILY (in `unknown_names` only) because it imports this module — the same shape
`health_cooldown` uses for the same cycle.
"""

from __future__ import annotations

#: A row whose every filing is a distinct thing that happened: its magnitude is a count.
INCIDENT = "incident"
#: A row that says one fact stayed true: its magnitude is the span it has stood for.
STANDING = "standing"

#: The `BLOCKED_EVENTS` names whose writers latch while a condition holds — see the module
#: docstring for the latch behind each. Every name here is a `BLOCKED_EVENTS` name (a test
#: pins it): one that drifted out of that set could never reach a row, and this module would
#: classify nothing while reading as if it did.
STANDING_EVENTS: frozenset[str] = frozenset({
    "backup_stale",
    "migration_due_soon",
    "migration_expired",
    "obligation_overdue",
    "answer_undelivered",
    "process_overhead_high",
})


def kind(event: str) -> str:
    """`STANDING` for a condition filed while it holds, else `INCIDENT`.

    An event this module does not know reads as an incident — the unit every row had before
    the split, and the one that counts facts rather than daemon uptime.
    """
    return STANDING if event in STANDING_EVENTS else INCIDENT


def unknown_names() -> set[str]:
    """Any name here that `BLOCKED_EVENTS` does not carry — empty is the correct reading."""
    from .health_stream import BLOCKED_EVENTS

    return set(STANDING_EVENTS) - set(BLOCKED_EVENTS)
