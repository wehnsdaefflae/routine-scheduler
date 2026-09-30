"""Maintain a codebase — keep repositories healthy and moving, one proven change at a time.

The mechanism: work reaches the routine from a QUEUE (decided work handed to it, defect reports
from other routines, the principal's messages and answers) and from the remote (new issues, pull
requests, a failing CI run). A SWEEP finds the drift nobody reported (failing tests, outdated
dependencies, dead links, stale dates and docs). Every change is made behind a checkpoint on a
fresh tree at the publishing branch's tip and proven on its own; then the whole tree passes the
routine's own GATE before anything is pushed. After the push the live result is checked where the
repository deploys. Content about the principal and public replies in his name wait for his
confirmation. A change too large for one run is a campaign on its own branch with its next
step recorded. Every report handed to the routine is answered in the run that handles it.

The routine keeps a snapshot of the remote (branch tips, open issues and pull requests, CI state)
in its findings record. A run compares the live remote against it, so the run gate can skip a
fire when nothing moved, nothing waits in the queue and the last full sweep is recent.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- version control on each repository (clone, branch, commit, push) under the identity the
  routine's config names;
- the forge's API for issues, pull requests and CI;
- the repository's own test and build tooling, run inside the routine's jail;
- a fetch of the live deployment, with a rendered-page look for a site;
- where the deployment needs a signal after a push (a service restart), the capability that
  gives it.

A routine that answers issues or speaks for the principal in public also holds the
correspondence and write-as-the-principal rules.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    REPOS,          # list[dict] — each repository: where the working copy comes from, the branch that publishes, where it deploys (a live URL, a release, a running service), the commit identity
    CONVENTIONS,    # list[str]  — in-repo documents stating binding house rules; re-read every run, they outrank habit and memory
    QUEUE,          # dict       — where work arrives (decided work in a designs file or in reports marked as decided, defect reports, answered questions, the principal's messages) and the campaign record for work spanning runs
    SWEEP,          # dict       — the passes a full sweep covers (tests, dependencies, links, metadata, content staleness) and the floor interval after which a full sweep is due regardless of events
    GATE,           # str        — the whole-tree gate the routine's own script runs before a push (quality checks over the full repository, the test suite, UI checks when UI changed)
    CONTENT_GATE,   # str        — what waits for the principal's confirmation before it goes live: claims, prose about him, public replies in his name
    FINDINGS,       # str        — the stable findings record: the remote snapshot the last run saw, known-and-accepted items with their reasons, open content proposals
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, edit_file, util, script, llm, spawn, subtask,
                             wait, view_image, memory_read, memory_write, ask_user, report,
                             finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Maintain a codebase",
    "slug": "maintain-a-codebase",
    "description": "Keep one or more repositories healthy and moving: take the queue and the "
                   "remote's events, sweep for drift on a floor cadence, make each change on a "
                   "fresh tree behind a checkpoint, prove it, gate the whole tree, push, check "
                   "the live result, gate content on the principal, answer every handed-in "
                   "report.",
    "when_to_use": "A standing mandate over code: build the decided-work queue of a project the "
                   "routine maintains, fix the defects other routines report against a shared "
                   "kit, keep a set of repositories or a deployed site healthy, run a funded "
                   "software project's engineering. Changes are test-gated and pushed by the "
                   "routine; claims about the principal wait for him. Work that spans runs is a "
                   "campaign. Not for one project with a counterparty and a status page "
                   "(steward-a-project), nor for a shared library of rules or utils curated from "
                   "usage (curate-a-library).",
    "version": 1,
    "tags": ["code", "maintenance", "repository", "tests", "deploy", "campaign"],
    "includes": ["change-scope", "make-failure-visible", "work-order", "verify-independently",
                 "audit-coverage", "git-checkpoint"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap  — the first run: working copies, the first remote snapshot, the findings record,
#                the gate script; then steady.
#   steady     — the queue and the sweep. Skip-eligible: the gate may skip a fire when the
#                remote matches the snapshot in FINDINGS, no queue item or answer waits and the
#                last full sweep is younger than SWEEP's floor.
#   campaign   — a change spanning runs is open on its own branch; every fire advances it, each
#                step through the gate, until it lands; then steady.
#   stand-down — the mandate ended (the repository is retired or handed over); one closing run.
PHASES = ["bootstrap", "steady", "campaign", "stand-down"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · take_queue — every queue item is worked this run, answered with why it waits, or carried "
    "in the campaign record with its next step",
    "d2 · sweep — every pass in SWEEP ran over the whole tree, with its findings listed",
    "d3 · verify_change — every change that landed was proven on its own: its covering test seen "
    "failing before the fix and passing after it, or the changed behaviour read back",
    "d4 · run_gate — the whole-tree gate passed on the exact tree that was pushed",
    "d5 · publish — every verified change is pushed under the configured identity, with the "
    "pushed commit named",
    "d6 · confirm_live — the live deployment shows the pushed change, read after the push",
    "d7 · propose_content — every content change is researched and filed as a question quoting "
    "the exact wording, or already waits on one",
    "d8 · answer_threads — every report handed to this routine is answered or closed; every public "
    "thread due a reply has one through CONTENT_GATE",
    "d9 · record — FINDINGS holds the snapshot this run saw, what was fixed and what was left with "
    "its reason; state/phase.json and state/gate.json are current",
]


class VerificationFailed(Exception):
    """A change did not survive its own proof or the whole-tree gate — it is fixed or reverted,
    never pushed."""


class NeedsConfirmation(Exception):
    """A change turns out to alter what the repository claims about the principal (CONTENT_GATE):
    it is researched and proposed with exact wording instead of applied."""


class ConventionViolation(Exception):
    """A planned change would break a rule in CONVENTIONS — the rule wins; the item is recorded
    as left, with the reason."""


def main():
    """One run: queue first, snapshot, sync, sweep when due, triage, change and prove, gate the
    tree, push, confirm live, propose content, answer every thread, record."""
    orient()

    if phase.current() == "stand-down":
        return stand_down()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through: the first run performs a real sweep, so the routine is useful at once.

    queue = take_queue()                        # the principal's words outrank the sweep
    if phase.current() == "stand-down":
        return stand_down()                     # the queue carried the end of the mandate

    snapshot = snapshot_remote()                # tips, issues, pull requests, CI against FINDINGS
    start = sync_working_copy()                 # fresh tree at the tip; `start` anchors a revert
    rules = read_conventions()
    findings = sweep(rules) if sweep_due(snapshot) else []
    todo = triage(queue, snapshot, findings)

    if todo.empty():
        record(start, applied=[], pushed=None)
        write_gate_state(snapshot)
        return finish("ok", "Nothing due: queue empty, remote unchanged, last sweep recent — "
                            "each named with how it was checked.")

    checkpoint()                                # an undo point before a dirty tree's first edit
    applied = []
    for item in todo.fix_now:                   # the campaign's next step, then what users hit
        try:
            change = apply_change(item, rules)
            verify_change(change)
            applied.append(change)
        except VerificationFailed:
            revert_change(item)                 # one failed change is reverted; the rest stands
        except NeedsConfirmation as content:
            todo.content.append(content)        # proposed below, never applied on its own
        except ConventionViolation as left:
            todo.left.append(left)              # the rule wins; recorded with the reason
        if not room_to_publish_cleanly():
            break                               # stop at a proven, publishable tree

    for proposal in todo.content:
        propose_content(proposal)

    pushed = None
    if applied:
        try:
            run_gate(applied)                   # the whole tree, exactly as it will be pushed
            pushed = publish(applied)
            confirm_live(pushed)
        except VerificationFailed:
            pushed = None                       # held back and named; nothing unproven is pushed

    answer_threads(queue)
    record(start, applied, pushed)
    write_gate_state(snapshot)
    return finish("ok", "queue items, sweep findings, changes with their proofs, the pushed "
                        "commit and its live check, proposals, threads answered")


def orient():
    """Read the state digest, FINDINGS and the campaign record before touching any repository.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers.
    FINDINGS is what stops the routine re-litigating settled items: an issue examined and
    deliberately accepted is reported as such, not rediscovered and refixed."""


def stand_down():
    """Close out the mandate: verify the live deliverable once more, name what stays open, stop.

    Check the live deployment itself, never the state files. Tell the principal where each
    repository and deployment stands, which commit was published last and what FINDINGS still
    holds open. The run that reads the end of the mandate does this at once. Start no sweep,
    apply no change, open no question."""
    return finish("ok", "Mandate closed: live state verified, last commit and open items named.")


def bootstrap():
    """First run: working copies, the first remote snapshot, the findings record, the gate script.

    Look each capability up in the CAPABILITIES catalog and write to memory which tool does what
    for each repository. Write GATE as the routine's own script, under a stable name that is not
    the run gate's scripts/gate.py. Seed FINDINGS with the snapshot and the known-and-accepted
    items. Then set the phase to steady and carry on into a real sweep."""


def take_queue():
    """Collect this run's queue: the principal's messages and answers first, then everything else.

    That is the decided work (the designs file, reports marked as decided), the defect reports
    handed to the routine and the campaign's recorded next step. The principal's requests are
    done in this run wherever feasible; one that cannot be done now is answered with why and
    carried forward, never dropped. A message ending the mandate (the repository retired or
    handed over) sets the phase to stand-down."""


def snapshot_remote():
    """Read the remote's state and compare it with the snapshot FINDINGS holds.

    Branch tips, open issues and pull requests with their last update, the CI conclusion on each
    tip. What moved since the last run is part of the queue."""


def sync_working_copy():
    """Take a fresh working copy at the publishing branch's tip and return the starting commit.

    Clone or update, never edit a stale tree. The starting commit is what a revert returns to and
    what the report quotes. In a campaign, the campaign's branch is brought up to date with the tip
    as well."""


def read_conventions():
    """Read every document in CONVENTIONS before this run's first edit; return the rules they set.

    They state the binding house rules (layout, styling constraints, index and metadata files to
    update alongside a change, the commit identity) and outrank habit, memory and this pattern."""


def sweep_due(snapshot):
    """True when a full sweep is due: the remote moved, code is queued, or SWEEP's floor passed."""


def sweep(rules):
    """Run every pass in SWEEP over the whole tree and return findings with evidence.

    Each finding carries a file and line or a fetched status code, a severity and a proposed fix.
    Every pass runs: a pass skipped for time reports a clean bill it has not earned. Settle with a
    machine what a machine can settle (parsing, link reachability, the test suite, dependency
    audits) and read the source for the rest. Before proposing a deletion, check every reference
    to it — configuration, indexes and server rules reference files a text search of the code
    misses."""


def triage(queue, snapshot, findings):
    """Split the work into fix_now, content and left, each item with its reason.

    fix_now holds technical changes the routine is trusted to make, the campaign's next step first,
    then what is broken for a user before what is merely untidy. content holds anything
    CONTENT_GATE covers. left holds items deliberately not touched — already accepted in FINDINGS,
    protected by CONVENTIONS, or costlier than the defect — each with its reason, so no later run
    rediscovers them. A change too large to prove within one run becomes a campaign: its own
    branch, its steps and the next one in the campaign record, the phase set to campaign until it
    lands."""


def checkpoint():
    """Commit an undo point before the first edit of a tree that holds uncommitted work.

    A clean tree at the tip needs none: the starting commit is the undo point."""


def apply_change(item, rules):
    """Make ONE surgical, anchored change; return what was touched and why.

    Change exactly what the item needs; reuse what exists before adding code. Refuse with
    ConventionViolation when the change would break a rule in CONVENTIONS. Refuse with
    NeedsConfirmation the moment a technical change turns out to alter what the repository
    claims. A deterministic step repeated every run (the gate, a snapshot, a site audit) is the
    routine's own script under a stable name."""


def verify_change(change):
    """Prove ONE change on its own before it counts, or raise VerificationFailed.

    A fix to a defect gets a test that is seen failing before the fix and passing after it. Any
    change is re-read, re-parsed and re-run where it runs. A claimed but unproven fix is worse than
    the defect it replaced."""


def revert_change(item):
    """Undo exactly that change and record why; the rest of the tree stays as proven."""


def room_to_publish_cleanly():
    """True while the budget can make and prove the next change and still gate, push and record.

    A boundary check, never a ration: the loop keeps taking items while items remain and stops at a
    proven, publishable tree rather than a half-applied one."""


def propose_content(proposal):
    """Research a content change at its sources, then file it as a question with the exact wording.

    Check the linked pages and the public record for what is true, with the source beside each
    fact. Draft the exact replacement text (in every language the repository carries) so the
    principal answers yes or no instead of composing it. An unanswered proposal stays in FINDINGS
    and the live text stays as it is."""


def run_gate(applied):
    """Run GATE over the whole tree exactly as it will be pushed, or raise VerificationFailed.

    The quality checks run over the full repository, not only the changed files; the test suite
    runs; UI checks run when UI changed. No secret, credential or private configuration may appear
    in the diff. The branch deploys what it receives, so the gate is the last check before users
    see the change."""


def publish(applied):
    """Commit under the identity the configuration names, push, and return the pushed commit.

    Verify the identity on the commits before the push. When the deployment needs a signal after a
    push (a restart, a release), give it only when this run pushed code."""


def confirm_live(pushed):
    """Check the deployment itself after the push: the live result shows the change.

    Fetch the live URL and each changed page, look at a changed page rendered, and read the CI
    conclusion on the pushed commit. A failed live check is reported first in the finish, with
    the revert to the starting commit proposed or made."""


def answer_threads(queue):
    """Answer every report handed to this routine in this run, then the public threads due a reply.

    A report is answered as fixed (with the commit), passed to its real owner, or waiting (with
    what on); a thread the answer ends is closed. Public issues and pull requests due a reply get
    one, drafted in the principal's voice and sent through CONTENT_GATE where it covers replies in
    his name."""


def record(start, applied, pushed):
    """Overwrite FINDINGS, append one LEDGER entry, set state/phase.json and keep state/ lean.

    FINDINGS gets this run's snapshot, what was fixed, what was left with its reason and the open
    content proposals. The LEDGER entry names the starting and pushed commits, the areas touched,
    the decisions and the candidates rejected with why. Record the phase under the key `phase`:
    the lifecycle label from PHASES, never the stage the run is in. Write these files with the file
    actions or the routine's own scripts; a write made through a shell command is invisible to the
    checks that read the run's actions. Rotate LEDGER.md in the run its size passes the byte cap
    the recipe names — derived from this routine's own entries, set above the size of the tail it
    keeps with room for several more — and when a rotation leaves the file over the cap or within
    one entry of it, fix the numbers with that measurement. A file under state/ is one a later run
    reads, under a stable name overwritten in place; a helper written for one run is deleted
    before finishing. Machinery that failed or misled you and that you worked around goes to its
    owner with `report`."""
    ledger.append("start and pushed commits, areas touched, decisions, rejected + why")


def write_gate_state(snapshot):
    """Write state/gate.json, the run gate's view of the repositories, from this run's findings.

    It holds the remote snapshot this run left (tips, open issue and pull request numbers with
    their last update, CI conclusions), the date of the last full sweep, the phase, and whether
    the queue holds anything the next fire must pick up. The gate only reads this file and the run
    is its only writer."""
