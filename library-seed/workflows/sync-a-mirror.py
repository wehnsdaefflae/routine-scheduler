"""Sync a mirror — keep an off-box copy of the instance's state current, one commit per run.

The mechanism: one export copies the SOURCE homes into a MIRROR repository, redacting
configuration on the way. The run accounts for every path the export removed, screens what is
about to leave the box against what must stay PRIVATE, commits, pulls and pushes, then reads the
remote head back. The happy path is a handful of actions and nothing more.

A push the remote refuses for a reason outside this routine's remit (a file its owner must shrink
or remove, a credential problem on the remote) is a BLOCKER: the run reports it to the owner
once, records it, and finishes partial on every later run until the owner clears it. The
mirror's history is never rewritten to get past a blocker. When a tool the export or the sync
depends on fails, the run fixes it at its cause when the tool is in its remit and reports it
otherwise; it never works around a broken tool silently.

There is no quiet night to skip: other routines change the source every day, while the export
itself is the only precise answer to "would anything change". So no phase is skip-eligible.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- the instance export, with read access to the homes it copies and the configuration it redacts;
- version control on the mirror repository with its remote's credential;
- where the routine repairs its tools, the capability to revise a shared util.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    SOURCE,      # str   — what is mirrored (the homes exported, the configuration redacted on the way) and where the export's exclusion list lives
    MIRROR,      # dict  — the mirror repository, its remote, the branch pushed
    PRIVATE,     # str   — what must never leave the box (credential files, private data such as photos or guest lists) and the ignore files that keep it out
    BLOCKERS,    # str   — where a known blocker is recorded with its owner and report id, so a run recognises it instead of re-diagnosing it
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, write_util, report, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Sync a mirror",
    "slug": "sync-a-mirror",
    "description": "Export the instance's state into a mirror repository, account for every "
                   "removed path, screen what leaves the box, commit, pull, push and read the "
                   "remote head back; report blockers to their owner and never rewrite history.",
    "when_to_use": "A nightly off-box copy: the routines, conversations and redacted "
                   "configuration exported into a git repository and pushed to its remote. The "
                   "run is mechanical and short, repairs the tools it depends on at the cause and "
                   "hands anything else to its owner. Not for maintaining the contents of what it "
                   "mirrors (maintain-a-codebase).",
    "version": 1,
    "tags": ["backup", "mirror", "git", "export", "nightly"],
    "includes": ["audit-coverage", "make-failure-visible", "change-scope"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap — the first run: the mirror repository and its remote checked, a first push; then
#               steady.
#   steady    — every run exports and pushes. Not skip-eligible.
#   blocked   — a push is refused for a reason outside this routine's remit. Every run still
#               exports and commits, tries the push once and finishes partial naming the owner's
#               report until the push goes through; then steady.
PHASES = ["bootstrap", "steady", "blocked"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · export — the export ran with its summary named, or its error recorded verbatim",
    "d2 · account_for_removed — every path the export removed is explained as benign or reported "
    "as unexplained",
    "d3 · screen_outgoing — the staged change was checked against PRIVATE, with every hit held "
    "back and reported",
    "d4 · push — the remote's head is this run's commit, read back after the push",
    "d5 · record_blocker — a refused push names its blocker, its owner and the owner's report",
    "d6 · record — the LEDGER entry names the commit and the counts copied and removed; "
    "state/phase.json names the phase",
]


class ToolFailure(Exception):
    """A tool the export or the sync depends on failed. It is fixed at its cause in this run when
    it is in the routine's remit, else reported to its owner; the run never works around it."""


class Blocker(Exception):
    """The remote refused the push for a reason outside this routine's remit. History stays as it
    is; the owner is told once and every later run finishes partial until it clears."""


def main():
    """One run: export, account for what the export removed, screen what is leaving, commit, pull,
    push and read the head back, record."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()

    try:
        summary = export()
    except ToolFailure as failure:
        repair_or_report(failure)               # at the cause, in this run
        summary = export()

    account_for_removed(summary)
    screen_outgoing()

    try:
        push()
    except Blocker as blocker:
        record_blocker(blocker)                 # recognised from BLOCKERS, reported once
        record(summary)
        return finish("partial", "Nothing pushed: the blocker, its owner, the report id.")

    if phase.current() == "blocked":
        phase.set("steady")                     # the owner cleared it; the push went through
    record(summary)
    return finish("ok", "the commit, counts copied and removed, the remote head")


def orient():
    """Read the state digest and BLOCKERS before exporting, so a known blocker is recognised.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers.
    Reports handed to the routine arrive with it and are answered in this run."""


def bootstrap():
    """First run: check the mirror repository and its remote, push once, then go steady.

    Look each capability up in the CAPABILITIES catalog and write to memory which tool exports and
    which syncs. Then set the phase to steady."""


def export():
    """Run the export over SOURCE into MIRROR and return its summary; raise ToolFailure on failure.

    The summary names what was copied and what was removed per source. An export error is
    recorded verbatim."""


def repair_or_report(failure):
    """Fix a failing tool at its cause when it is in this routine's remit, else report it.

    A fix is a revision of the shared tool with its selftest passing, not a workaround in this
    routine. A tool outside the remit goes to its owner with the failure and the run it happened
    in."""


def account_for_removed(summary):
    """Explain every path the export removed as benign, or report it as unexplained.

    Benign means the source itself removed it (a routine archived, a file rotated), checked against
    the live directory. An unexplained removal is reported to the owner of that path; the push
    goes ahead, since the mirror's history keeps what it had."""


def screen_outgoing():
    """Check the staged change against PRIVATE before it leaves the box.

    Credential files, key material and private data are held back from the commit and reported to
    the owner of the directory they came from, with the ignore-file line that would keep them
    out. The rest of the change goes ahead."""


def push():
    """Commit, pull, push, then read the remote head back; raise Blocker on a refusal.

    A conflict aborts the sync rather than resolving it by force. A refusal whose cause is inside
    this routine's remit is fixed and the push retried; one outside it is a Blocker."""


def record_blocker(blocker):
    """Record the blocker in BLOCKERS, report it to its owner once, set the phase to blocked.

    A blocker already in BLOCKERS is recognised, not re-diagnosed or re-reported: the run names its
    owner and report id."""


def record(summary):
    """Append one LEDGER entry and set state/phase.json.

    The entry names the commit, the counts copied and removed, anything held back and any blocker.
    Record the phase under the key `phase`: the lifecycle label from PHASES, never the stage the
    run is in. Rotate LEDGER.md in the run its size passes the byte cap the recipe names — derived
    from this routine's own entries, set above the size of the tail it keeps with room for several
    more — and when a rotation leaves the file over the cap or within one entry of it, fix the
    numbers with that measurement."""
    ledger.append("commit, copied / removed counts, held back, blocker")
