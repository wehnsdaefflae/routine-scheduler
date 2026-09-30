"""Do one job — one fire, one bounded job: research it, deliver it, never do it twice.

The mechanism: the routine exists to perform one kind of JOB each time it is fired — by a
one-shot run another routine armed for a dated item, by the principal's click, or by a date. The
fire's BRIEF names the item (the arming note, the text typed with the run); a fire without a
brief takes the next unhandled item from the source. The run researches the item's facts from
live SOURCES, produces the deliverable, checks it against those facts, delivers it and records
the item's key in HANDLED, so the same job is never done twice. A fire that finds no item says
what it searched and stops.

A one-shot fire and a click bypass the run gate, so this mechanism needs none. The brief is what
the run is accounted against.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- read access to where items come from (a mailbox read without marking anything, a list);
- the sources the item's facts are researched in, live ones before stored copies;
- the DELIVERY channel (a message to the principal, a file in an agreed place) and its
  confirmation.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    JOB,         # str        — the one kind of job a fire performs and the deliverable it leaves
    BRIEF,       # str        — where a fire's brief comes from and how a fire without one finds the next unhandled item
    SOURCES,     # list[str]  — where the item's facts are researched, live sources before stored copies
    DELIVERY,    # dict       — the channel the deliverable goes through and what confirms it arrived
    HANDLED,     # str        — the state file keyed by item, holding each delivery's confirmation
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, llm, view_image, ask_user, report, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Do one job",
    "slug": "do-one-job",
    "description": "Perform one bounded job per fire: take the item the brief names (or the next "
                   "unhandled one), research its facts from live sources, produce and check the "
                   "deliverable, deliver it with a confirmation, record the item so it is never "
                   "done twice.",
    "when_to_use": "A worker fired for one item at a time: a one-shot run another routine arms "
                   "before a dated event, a job the principal starts with a click, a task tied to "
                   "a date. Each fire is complete in itself; nothing carries over but the record "
                   "of what was handled. Not for a watch that finds the items and arms the fires "
                   "(watch-and-report), nor for a queue worked through in batches "
                   "(drain-a-work-queue).",
    "version": 1,
    "tags": ["worker", "one-shot", "event", "delivery"],
    "includes": ["verify-independently"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   steady — every fire is one job; there are no cross-run milestones. Not skip-eligible: every
#            fire is either briefed or a deliberate click.
PHASES = ["steady"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · take_brief — the job's item is named from the brief, or found as the next unhandled item "
    "in the source",
    "d2 · research — every fact the deliverable states comes from a source read this run, named "
    "beside it",
    "d3 · check — the deliverable was read back against the facts it states before it went out",
    "d4 · deliver — the deliverable went out through DELIVERY, with the channel's confirmation",
    "d5 · record — HANDLED holds the item's key with that confirmation",
]


class ExternalBlocker(Exception):
    """A source or the delivery channel cannot be reached now. Nothing is delivered from a guess;
    the gap is named and the item stays unhandled."""


def main():
    """One fire: take the brief, skip what is already handled, research, produce, check, deliver,
    record."""
    orient()

    item = take_brief()
    if item is None:
        record(None)
        return finish("ok", "No job: the brief named none and no unhandled item exists; what was "
                            "searched.")
    if already_handled(item):
        return finish("ok", "Already handled: the item, when, with which confirmation.")

    try:
        facts = research(item)
        deliverable = produce(item, facts)
        check(deliverable, facts)
        deliver(deliverable)
    except ExternalBlocker:
        record(item)
        return finish("partial", "The item, the source or channel that could not be reached, "
                                 "nothing delivered.")

    record(item)
    return finish("ok", "the item, the deliverable, the confirmation")


def orient():
    """Read the state digest and HANDLED before looking for an item.

    The digest carries the brief of a one-shot fire (the note the arming routine left), the
    principal's message on a click, the last result and the LEDGER tail."""


def take_brief():
    """Name this fire's item: the one the brief names, else the next unhandled one in the source.

    An item named by the brief is taken as named, without re-deriving it from the source. With no
    brief, list the source and take the soonest item not in HANDLED. Return None when there is
    none, with what was searched."""


def already_handled(item):
    """True when HANDLED already holds this item's key with a delivery confirmation."""


def research(item):
    """Gather the facts the deliverable states from SOURCES, live ones first; return them sourced.

    Each fact carries the source it came from and when it was read. A stored copy stands in only
    when the live source cannot answer; the deliverable then says so. Raise ExternalBlocker when no
    source can answer a fact the deliverable cannot do without."""


def produce(item, facts):
    """Produce the deliverable from the facts, in the form DELIVERY expects and the reader needs."""


def check(deliverable, facts):
    """Read the deliverable back against the facts before it goes out; fix what does not match."""


def deliver(deliverable):
    """Send the deliverable through DELIVERY and keep the channel's confirmation.

    A send without its confirmation (an exit code, a message id) is not delivered; raise
    ExternalBlocker."""


def record(item):
    """Write the item's key and confirmation to HANDLED, append one LEDGER entry, set the phase.

    The entry names the item, the deliverable, the confirmation or what blocked it. Record the phase
    under the key `phase`. A file under state/ is one a later fire reads, under a stable name
    overwritten in place."""
    ledger.append("item, deliverable, confirmation or blocker")
