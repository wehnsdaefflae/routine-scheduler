"""Answer a mailbox — serve one mailbox as the principal's agent: classify, filter, correspond.

The mechanism: every run scans every watched FOLDER of one MAILBOX across a look-back WINDOW and
classifies each message it has not seen before (a new counterpart of the kind the routine serves,
a reply in a thread it is running, a bounce of something it sent, mail it must leave alone). It
keeps the server-side FILTER current with what it learned, moves each thread on with the next
move from its PLAYBOOK, and sends the drafts the principal approved under his standing rule.
Per-thread state carries the correspondence across runs, so a thread resumes at the right stage
and no counterpart is answered twice.

A run with no new mail in any folder is the cheap idle of this mechanism: the scan establishes it
in a few reads (every folder listed across the window, the unfiltered newest page of each read to
prove the listing complete) and the run finishes. The run leaves the routine's run gate what it
needs to skip such a fire without starting a run: the folders, how far the scan reached, the
senders the gate may ignore and anything classified but not yet answered.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- read access to the mailbox's folders, by message id, without marking anything read;
- the outbox verbs the playbook uses (write a draft, send an approved draft, delete a draft this
  routine wrote), held for exactly those verbs;
- the server-side filter: read the live script, validate a new one, activate it;
- a writing check for drafts that go out under the principal's name.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    MAILBOX,     # str        — the account and how the run reaches it
    FOLDERS,     # list[str]  — every folder scanned each run (the inbox, the junk folder, wherever replies land)
    CLASSES,     # dict       — what a message can be (a new counterpart this routine serves, a reply in a running thread, a bounce, mail to leave alone) and the tells of each
    WINDOW,      # str        — how far back a scan looks, in days and in a minimum count per folder, so a message an earlier run missed is still seen
    FILTER,      # dict       — the server-side filter: the rules this routine owns, the principal's own rules it keeps, how a new script is validated before it goes live
    THREADS,     # str        — the per-thread state file: counterpart, stage, message ids, moves used, the ids of drafts this routine wrote
    PLAYBOOK,    # list[str]  — the moves a thread advances through, so a thread never repeats one
    OUTBOX,      # dict       — where drafts wait for the principal and his approval rule (e.g. a draft left in place past one run is approved, a deleted draft is a veto)
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, edit_file, util, script, llm, ask_user,
                             report, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Answer a mailbox",
    "slug": "answer-a-mailbox",
    "description": "Serve one mailbox: scan every watched folder, classify what is new, keep the "
                   "server-side filter current, move each correspondence thread on with its next "
                   "move, send the drafts the principal approved, keep per-thread state.",
    "when_to_use": "A standing mail service the principal delegates: triage a class of incoming "
                   "mail, keep a server-side filter learning from it, and run multi-round "
                   "correspondence with the senders (a tar-pit for predatory solicitations, a "
                   "reply service for a recurring kind of request), with drafts the principal "
                   "can veto before they go. Quiet days are the norm and a run gate can skip "
                   "them. Not for watching a mailbox only to report on it (watch-and-report).",
    "version": 1,
    "tags": ["mail", "correspondence", "filtering", "triage", "outbox"],
    "includes": ["write-as-the-principal", "correspondence", "audit-coverage", "change-scope"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap — the first run: learn the account's folders and its live filter (so the
#               principal's own rules are known and kept), seed THREADS; then steady.
#   steady    — every later run. Skip-eligible: the gate may skip a fire when no folder holds a
#               message newer than the last scan from a sender outside the ignore list, no bounce
#               arrived and state/gate.json carries nothing over.
PHASES = ["bootstrap", "steady"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · send_approved — every draft this routine wrote that OUTBOX's rule counts as approved is "
    "sent and removed from the outbox by its recorded id, with the sent copy recorded in its "
    "thread",
    "d2 · scan — every folder in FOLDERS was listed across WINDOW with its unfiltered newest page "
    "read; every message in it is classified or already in THREADS",
    "d3 · refresh_filter — every sender and tell classified this run is covered by the live "
    "filter, whose previous version is kept",
    "d4 · open_thread — every new counterpart has its opening move drafted in its own thread",
    "d5 · advance_thread — every reply in a running thread has its next move drafted, or is named "
    "with what it waits on",
    "d6 · record — THREADS holds each thread's stage and message ids; state/phase.json is current",
    "d7 · write_gate_state — state/gate.json holds how far the scan reached per folder and what "
    "the next run must pick up",
]


class NeedsDecision(Exception):
    """A choice only the principal can make (a sender he may actually know, a policy question) —
    filed as a deferred question while the rest of the run goes on."""


class Waiting(Exception):
    """A thread's next move needs the principal or the counterpart first — it is skipped this run
    and stays open in THREADS."""


def main():
    """One run: send what the principal approved, scan every folder, keep the filter current, move
    every thread on, record, leave the gate what it needs."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through: the first run already classifies and answers what the folders hold.

    send_approved()                             # earlier drafts approved under OUTBOX's rule
    fresh, replies, bounces = scan()
    waiting = carried_over()
    if not fresh and not replies and not bounces and not waiting:
        record()
        write_gate_state()
        return finish("ok", "No new mail: each folder named with how far the scan reached.")

    refresh_filter(fresh)
    for message in fresh + waiting:
        try:
            open_thread(message)
        except NeedsDecision as decision:
            ask_user(decision, mode="deferred")
    for thread in replies + bounces:
        try:
            advance_thread(thread)
        except Waiting:
            continue
        except NeedsDecision as decision:
            ask_user(decision, mode="deferred")

    record()
    write_gate_state()
    return finish("ok", "senders classified, filter changes, drafts written, sends, threads moved")


def orient():
    """Read the state digest, THREADS and memory's tool notes before touching the mailbox.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers.
    THREADS says where each thread stands, so no counterpart is answered twice and every thread
    resumes at its stage."""


def bootstrap():
    """First run: learn the folders and the live filter, seed THREADS, then go steady.

    Look each capability up in the CAPABILITIES catalog and write to memory which tool reads the
    folders, which writes drafts and which maintains the filter. Read the live filter script so
    every rule the principal wrote himself is known and kept. File a deferred question for any
    sender he may genuinely correspond with. Then set the phase to steady and carry on."""


def send_approved():
    """Send every draft this routine wrote that OUTBOX's rule counts as approved, then clear it.

    Identify drafts by the ids THREADS recorded when they were written, never by folder
    position, so a draft the principal wrote himself is never touched. Send it, remove that
    draft by its id, confirm the sent copy exists and record it in its thread. A draft the
    principal deleted is a veto: record it in the thread and leave the counterpart unanswered."""


def scan():
    """List every folder in FOLDERS across WINDOW and classify every message not in THREADS.

    Classify by CLASSES: a borderline case is one scoped judgment; mail from the principal's real
    contacts is left alone. Then read the newest page of each folder unfiltered to prove the
    listing complete: a date-bounded or filtered listing can miss a message that arrived out of
    order. Return (fresh, replies, bounces)."""


def carried_over():
    """Messages an earlier run classified but did not answer, from state/gate.json's carry-over."""


def refresh_filter(fresh):
    """Fold this run's newly classified senders and tells into the server-side filter.

    Read the LIVE script first and change only the rules this routine owns; every rule the
    principal wrote stays. Keep the previous version, validate the new script, then activate it.
    A script that fails validation is not activated: the previous one stays live and the failure
    is named in the finish."""


def open_thread(message):
    """Draft the opening move for ONE new counterpart inside its own thread and record it.

    Reply headers set, the subject kept, the original quoted. Written as the principal writes
    (first person, his register, short), then checked with the writing capability. The thread
    opens in THREADS at the first stage with the draft's id."""


def advance_thread(thread):
    """Answer the counterpart's latest message with the next PLAYBOOK move not yet used in it.

    One move per message received, since each move answers what the counterpart just wrote.
    Drafted inside the thread, checked with the writing capability, its id recorded in THREADS.
    A bounce of a sent message closes the thread or re-routes it. Raise Waiting when the next move
    needs the principal or the counterpart first."""


def record():
    """Update THREADS, append one LEDGER entry, set state/phase.json and keep state/ lean.

    The entry names the senders classified, the filter's change, the drafts written, what was
    sent and the vetoes seen. Record the phase under the key `phase`: the lifecycle label from
    PHASES, never the stage the run is in. Write these files with the file actions or the
    routine's own scripts; a write made through a shell command is invisible to the checks that
    read the run's actions. Rotate LEDGER.md in the run its size passes the byte cap the recipe
    names — derived from this routine's own entries, set above the size of the tail it keeps with
    room for several more — and when a rotation leaves the file over the cap or within one entry
    of it, fix the numbers with that measurement. Keep one previous version of the filter script,
    not one per run. A file under state/ is one a later run reads, under a stable name overwritten
    in place; anything only this run needed is deleted before finishing. Machinery that failed or
    misled you and that you worked around goes to its owner with `report`."""
    ledger.append("classified, filter change, drafts, sent, vetoes")


def write_gate_state():
    """Write state/gate.json, the run gate's view of this mailbox, from this run's scan.

    Per folder the time the scan reached, the sender domains the gate may ignore (the principal's
    own institution, known correspondents) and the carry-over list of messages classified but not
    answered. The gate only reads this file and the run is its only writer."""
