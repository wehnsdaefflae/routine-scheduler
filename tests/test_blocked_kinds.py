"""Which blocked-work events are STANDING CONDITIONS, and which are INCIDENTS (R3418).

`health_stream.BLOCKED_EVENTS` means "work that was due did not happen", and the fold used to
report every row's magnitude as a `count`. For an incident that is a number of facts — a fire
refused twice is two refusals. For a standing condition it is the number of DAYS THE DAEMON
HAPPENED TO BE UP while one fact stayed true, because each of those writers latches privately
to one row a day: a nineteen-night backup gap rendered as `count: 19` beside `fire_refused: 19`
and both were summed into one `total`.

These tests pin the split in both directions. The dangerous drift is an event classified by
habit rather than by its writer's latch, so every name is listed here explicitly — a new
`BLOCKED_EVENTS` entry fails `test_kind_answers_for_every_blocked_event` until someone decides
which kind it is.
"""

from rsched.readmodels import blocked_kinds
from rsched.readmodels.blocked_kinds import INCIDENT, STANDING, STANDING_EVENTS, kind
from rsched.readmodels.health_stream import BLOCKED_EVENTS

#: The writers that latch to one row a day (or one per latch window) while one fact holds, with
#: where the latch lives. A `count` over these rows measures daemon uptime, not severity.
LATCHED_BY_THE_DAY = {
    "backup_stale": "daemon/backup_watch `_last_filed` — at most once a day while it holds",
    "migration_due_soon": "daemon/marker_watch — one row a day while the deadline stands",
    "migration_expired": "daemon/marker_watch — one row a day while the deadline stands",
    "obligation_overdue": "daemon/owed_watch — one row per routine per day",
    "answer_undelivered": "daemon/owed_watch — one row per routine or conversation per day",
    "process_overhead_high": "engine/overhead_alarm LATCH_HOURS = 24, read back off the stream",
}

#: Events whose every row is a distinct thing that happened, so counting them is counting facts.
#: `trigger_capped` and `scheduler_tick_error` are the two that look latched and are not: a cap
#: is re-earned each day by real trigger traffic ("one event per day AT THE TRANSITION", F276),
#: and `tickguard` files one row per failure EPISODE, not per day it lasts. `postdeploy_failed`
#: is once per process by construction — each boot that served the wrong build is its own fact.
COUNTABLE_INCIDENTS = (
    "fire_refused", "lane_fire_refused", "fire_paused", "lane_fire_paused", "oneshot_skipped",
    "lane_chain_stopped", "lane_chain_member_skipped", "scheduler_tick_error",
    "finish_gate_failed", "trigger_capped", "commit_failed", "item_dropped_live",
    "wrong_turn_detected", "model_chain_exhausted", "postdeploy_failed",
)


def test_every_standing_name_is_a_blocked_event():
    """The fold keeps only `BLOCKED_EVENTS`, so a name that drifted out of that set could never
    reach a row — this module would then classify nothing, silently, exactly the way
    `operator_blocks.unknown_names` guards against.
    """
    assert blocked_kinds.unknown_names() == set()
    for name in STANDING_EVENTS:
        assert name in BLOCKED_EVENTS, name


def test_the_writers_that_latch_by_the_day_are_standing():
    """Each of these files one row a day while ONE fact holds — so the row count is uptime."""
    for name, why in LATCHED_BY_THE_DAY.items():
        assert kind(name) == STANDING, f"{name} is latched: {why}"


def test_what_happens_once_per_occurrence_stays_countable():
    """Doctrine 2's case, which this change must not touch: an incident that happened twice is
    two facts, so its magnitude stays a count and suppressing one would understate the fleet.
    """
    for name in COUNTABLE_INCIDENTS:
        assert kind(name) == INCIDENT, name
        assert name not in STANDING_EVENTS, name


def test_kind_answers_for_every_blocked_event():
    """No third kind, and no unclassified event: a new name in `BLOCKED_EVENTS` is a decision
    someone has to make here, not a default they can drift into.
    """
    assert set(LATCHED_BY_THE_DAY) | set(COUNTABLE_INCIDENTS) == set(BLOCKED_EVENTS), (
        "a BLOCKED_EVENTS name is unclassified here — decide whether its writer latches while "
        "a condition holds (standing) or files one row per occurrence (incident)")
    for name in BLOCKED_EVENTS:
        assert kind(name) in (INCIDENT, STANDING), name
    assert set(STANDING_EVENTS) == set(LATCHED_BY_THE_DAY)


def test_an_unknown_event_is_not_silently_standing():
    """A name the fold never carries reads as an incident, the kind whose magnitude is a count —
    the reading that was true for every event before this split.
    """
    assert kind("not_an_event") == INCIDENT
