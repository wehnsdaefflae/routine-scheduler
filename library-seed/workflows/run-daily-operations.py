"""Run daily operations — carry out a standing duty that has work every day by construction.

The mechanism: the routine operates something on the principal's behalf, day after day, with no
counterparty and no end: acquiring and syncing media against a watch list, keeping an account's
presence alive in a community, rolling a personal dashboard over to the new day from fresh
telemetry. Each run first takes in what completed since the last run (downloads to pull, verify
and clear, replies to read), then the principal's requests, then reads its INPUTS and carries out
the day's DUTY, checking the CAPACITY each step consumes before it consumes it and verifying the
external effect after. Sub-duties on their own cadence (a weekly recommendation pass, a docs
refresh) run when their recorded date comes round. With the day's duty done, the run takes the
next improvement the backlog holds.

There is no quiet day to skip: the date itself makes work, so no phase is skip-eligible for a
run gate. A fire started for one request (a follow-up asking for one title) does that request and
stops; the day's full duty belongs to the scheduled fire.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- read access to each input (a remote client's state, a telemetry API, a community's feed);
- the outward verbs the duty uses (add a download, post or vote as the account, publish the day's
  page), with the account's credential kept in the secret store, never in memory or the recipe;
- the checks that verify the effect (file size and type, a read-back of each write, the live
  page's markers);
- the capacity reads (free disk, a quota, a rate limit).

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    DUTY,        # str        — the standing daily duty and the external effect it leaves (files acquired and synced, posts written, a dated page rolled over)
    INPUTS,      # list[dict] — the streams read each run (a watch list, telemetry, a remote client's state, a feed) and how "new" is tracked in each
    REQUESTS,    # str        — how the principal asks for something (a chat message, a request queue) and where open requests are kept
    CAPACITY,    # dict       — what the duty consumes (disk space, a quota, a rate limit) and the check that runs before each step consuming it
    PERIODIC,    # list[dict] — duties on their own cadence (a weekly recommendation pass, a documentation refresh), each with the date it last ran
    VERIFY,      # str        — how the external effect is verified (byte and type checks on files, a read-back of each write, the live page's markers)
    VOICE,       # str        — optional: the account and voice outward writes use, kept consistent across runs
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, edit_file, util, script, llm, view_image,
                             memory_read, memory_write, ask_user, report, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Run daily operations",
    "slug": "run-daily-operations",
    "description": "Carry out a standing daily duty on the principal's behalf: take in what "
                   "completed since the last run, do his requests, read the inputs, perform the "
                   "day's duty with capacity checked before and the effect verified after, run "
                   "the periodic sub-duties that are due, then improve.",
    "when_to_use": "An operator job with work every day and no end: acquire and sync media "
                   "against a watch list, keep an account present and active in a community, "
                   "refit models on the day's telemetry and roll a personal dashboard to the new "
                   "date. The principal mostly steers by occasional requests. Every fire has "
                   "work, so no run gate. Not for a queue that ends when empty "
                   "(drain-a-work-queue), nor for a project with a counterparty "
                   "(steward-a-project).",
    "version": 1,
    "tags": ["operations", "daily", "operator", "presence", "sync"],
    "includes": ["audit-coverage", "verify-independently", "make-failure-visible",
                 "feedback-loop"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap — the first run: find the tools, do the one-time setup (an account claimed, a
#               client linked) and record it, carry out a first day's duty; then steady.
#   steady    — every later run does the day's duty. The one-time setup is never re-checked
#               here. No phase is skip-eligible: the date itself makes work.
PHASES = ["bootstrap", "steady"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · drain_completed — everything that completed since the last run is taken in, verified and "
    "cleared at its source",
    "d2 · take_requests — every request from the principal is done and verified, or answered with "
    "what it waits on",
    "d3 · read_inputs — every input in INPUTS was read this run, each with what it yielded or why "
    "it could not be read",
    "d4 · do_step — today's duty produced its external effect, each part verified by VERIFY",
    "d5 · run_periodic — every periodic duty whose date came round ran, with its date moved",
    "d6 · record — the LEDGER entry names today's effect; state/phase.json and the state files let "
    "the next run resume",
]


class CapacityShort(Exception):
    """The capacity a step consumes is not there (a full disk, a spent quota): the step waits, is
    named in the finish and is retried next run."""


class ExternalBlocker(Exception):
    """An input or the outward service cannot be reached now. Prior state stays intact; the gap is
    named."""


def main():
    """One run: finish what completed, do the principal's requests, read the inputs, carry out the
    day's duty (capacity checked, effect verified), run the periodic duties, improve, record."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()                             # the one-time setup lives here, never in steady

    drain_completed()                           # FIRST: what completed since the last run
    take_requests()
    if started_for_a_request():
        # A fire started for one request does that request and stops: the day's full duty
        # belongs to the scheduled fire. The request is what this run is accounted against.
        record()
        return finish("ok", "the request, what was done, how it was verified")

    inputs = read_inputs()
    for step in todays_duty(inputs):
        try:
            check_capacity(step)
            do_step(step)
            verify_effect(step)
        except CapacityShort:
            continue                            # named in the finish, retried next run
        except ExternalBlocker:
            continue

    run_periodic()
    improve()
    record()
    return finish("ok", "today's effect, requests handled, periodic duties run, what waits")


def orient():
    """Read the state digest, the state files and memory's tool notes before acting.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers.
    The state files hold the watch list, the last snapshot of what is in flight and the dates the
    periodic duties last ran."""


def bootstrap():
    """First run: find the tools, do the one-time setup and record it, then carry out a first day.

    Look each capability up in the CAPABILITIES catalog and write to memory which tool does what.
    Do the one-time setup the duty needs (claim the account, link the client) and record that it
    is done, so no later run checks it again. Then set the phase to steady and carry on."""


def drain_completed():
    """FIRST: take in everything that completed since the last run before starting anything new.

    A finished download is pulled, verified by VERIFY (exact size, file type) and only then
    cleared at its source; replies to yesterday's posts are read; last night's telemetry is
    ingested. An item that fails verification stays at its source and is named."""


def take_requests():
    """Do every request the principal made since the last run, verified, or say what it waits on.

    Requests arrive as messages or in the request queue. A request is done in the run that reads
    it wherever feasible; one that cannot be done yet is recorded with what it waits on and
    answered so. A deferred question he answered counts as a request."""


def started_for_a_request():
    """True when this fire was started for one request rather than by the schedule."""


def read_inputs():
    """Read every input in INPUTS for what is new since the last run and return it.

    A watch list is checked item by item; an item whose source cannot answer (no listing for it)
    carries a recorded date after which it is checked again. An input that could not be read is
    named as unknown, never treated as quiet."""


def todays_duty(inputs):
    """The steps of today's duty, derived from the inputs and the requests still open.

    For each item on the watch list that has something new, the step that acquires it; for the
    day's presence, the reads and writes the voice calls for; for the dated page, the refit and
    the rollover."""


def check_capacity(step):
    """Check the capacity the step consumes before it consumes it; raise CapacityShort if short.

    Free disk before a download or a pull, the remaining quota before a batch of calls, the rate
    limit before a burst of writes. The check is a script of the routine's own, run the same way
    every time."""


def do_step(step):
    """Carry out ONE step of the duty and produce its external effect.

    Outward writes use VOICE consistently. A judgment-free step repeated every run (a transfer
    with its checks, a model refit, a page render) runs as the routine's own script under a
    stable name; a command run by hand every day is a script waiting to be written."""


def verify_effect(step):
    """Verify the step's external effect by VERIFY before counting it as done.

    A transferred file by exact size and file type; a write by reading it back from the service;
    the live page by its markers for today's date. An unverified effect is named, never assumed."""


def run_periodic():
    """Run every periodic duty whose date came round, then move its date.

    A weekly recommendation pass, a refresh of the service's documentation, a clean-up of what the
    duty left behind. Not due when no date came round."""


def improve():
    """With the day's duty done and verified, take the next improvement the backlog holds.

    A better method, a new signal, a script replacing a repeated manual step: land it verified
    while the budget allows, or leave it on the backlog with its next step."""


def record():
    """Update the state files, append one LEDGER entry, set state/phase.json and keep state/ lean.

    The entry names today's effect, the requests handled, the periodic duties run and what waits.
    Record the phase under the key `phase`: the lifecycle label from PHASES, never the stage the run
    is in. Write every state file with the file actions or the routine's own scripts; a write made
    through a shell command is invisible to the checks that read the run's actions. Rotate
    LEDGER.md in the run its size passes the byte cap the recipe names — derived from this
    routine's own entries, set above the size of the tail it keeps with room for several more —
    and when a rotation leaves the file over the cap or within one entry of it, fix the numbers
    with that measurement. A file under state/ is one a later run reads, under a stable name
    overwritten in place; snapshots and screenshots only this run needed are deleted before
    finishing. Machinery that failed or misled you and that you worked around goes to its owner
    with `report`."""
    ledger.append("today's effect, requests handled, periodic duties, what waits")
