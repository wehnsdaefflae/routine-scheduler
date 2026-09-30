"""Curate a library — keep shared rules, tools or recipes sound from how their holders use them.

The mechanism: a LIBRARY of shared items (general rules, utils, routine recipes, workflow patterns)
is used by many HOLDERS; a change to one item lands on all of them at once. Each pass computes
by script every item's holders and its fresh EVIDENCE since its last review (runs that exercised
it, failures that name it, reports about it), selects the items whose evidence is fresh, reads
how the holders actually used them, diagnoses, and changes the shared item under its approval
dial. A change that touches holders migrates them or tells them. The per-item REVIEWED record
keeps the next pass from re-reviewing an item with nothing new to say. Defects other routines
hand in are fixed and answered in the pass that takes them.

Because every change reaches every holder, the proof bar is high: an item changes on evidence
from named runs, a change is live only after the item's own test and the library's lint pass,
and an item is removed only for a reason beyond disuse (merged into another, broken beyond
repair, duplicated) with every reference to it settled first. Evidence has to separate an item
that works from one nobody needs: how often it is reached, how often it fires when it should
not, which corrections it failed to prevent.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- read access to the holders' configs and run transcripts, across routines and conversations;
- the write action for the library's kind of item, under its approval dial;
- the removal action, where items are removed;
- the library's lint and each item's own test;
- addressed reports, to every holder a change touches.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    LIBRARY,     # dict  — the shared items curated (rules, utils, recipes, patterns): where they live, the action that writes one with its approval dial, the lint that gates a change
    HOLDERS,     # str   — how an item's holders are found (configs naming it, calls, includes, allowlists)
    EVIDENCE,    # dict  — where use is observed (transcripts, usage streams, failures, reports) and the routine's own inventory script that counts each item's fresh evidence since its last review
    REVIEWED,    # str   — the per-item review record: last pass, verdict, last change, the runs it rests on
    TARGET,      # str   — optional: a campaign's measurable target (a registry size, every holder migrated off an item)
    INTAKE,      # str   — how other routines hand in defects (addressed reports) and how each is closed
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, edit_file, util, script, llm, spawn, wait,
                             subruns, read_rule, write_rule, write_util, remove_util,
                             memory_read, memory_write, ask_user, report, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Curate a library",
    "slug": "curate-a-library",
    "description": "Keep a shared library (rules, utils, recipes, patterns) sound from usage "
                   "evidence: inventory every item's holders and fresh evidence by script, review "
                   "the items with something new to say, change them under their approval dial "
                   "and their tests, migrate or tell every holder, record a verdict per item.",
    "when_to_use": "A maintenance routine that owns one shared layer many routines hold: the "
                   "general rules, the util registry, the routines' own recipes or the pattern "
                   "library. It reads how holders actually used each item, changes the shared "
                   "copy when the evidence says so and hands everything else to its owner. A "
                   "multi-pass consolidation toward a measurable target is a campaign. Not for "
                   "code with its own test suite and deployment (maintain-a-codebase), nor for "
                   "proposals someone else applies (audit-and-propose).",
    "version": 1,
    "tags": ["curation", "library", "evidence", "maintenance", "fleet"],
    "includes": ["audit-coverage", "change-scope", "make-failure-visible",
                 "verify-independently"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap — the first pass: the inventory script written, REVIEWED seeded; then steady.
#   steady    — evidence-driven passes. Skip-eligible: the gate may skip a fire when no item has
#               fresh evidence since its last review, no report waits and no answer is pending.
#   campaign  — a change spanning passes toward TARGET is under way; every fire advances it until
#               TARGET is met, then steady.
PHASES = ["bootstrap", "steady", "campaign"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · take_intake — every report handed to this routine is fixed and answered, answered with "
    "why it waits, or passed to its owner",
    "d2 · run_inventory — the inventory ran over the whole library this run, with each item's "
    "holders and fresh evidence counted",
    "d3 · diagnose — every selected item has a verdict citing the runs it rests on: changed, "
    "removed with its reason, or standing with its reason",
    "d4 · change_item — every change this run is live in the library after the item's own test "
    "and the lint passed",
    "d5 · notify_holders — every holder a change touches is migrated, or told in an addressed "
    "report",
    "d6 · record — REVIEWED holds this pass's verdicts; state/phase.json is current",
    "d7 · write_gate_state — state/gate.json holds the inventory fingerprint and the "
    "fresh-evidence counts the gate compares",
]


class GateRefused(Exception):
    """The item's own test or the lint refused the change: the item stays as it was. The refusal
    is recorded with its diagnostics and passed on when its cause is not this routine's."""


def main():
    """One pass: close what was handed in, inventory by script, select the items with fresh
    evidence, triage them in one read-only child, study and diagnose each, change it under its
    dial, tell its holders, lint, record the verdicts."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through: the first pass reviews real items.

    take_intake()                               # defects handed in: fixed and answered in this pass
    inventory = run_inventory()                 # own script: items, holders, fresh evidence
    selected = select(inventory)                # campaign items, flagged items, then the stalest

    if not selected:
        record()
        write_gate_state(inventory)
        return finish("ok", "No item has fresh evidence since its last review; the intake is "
                            "closed.")

    flags = triage(selected)                    # one read-only child over every candidate
    for item in selected:
        evidence = gather_evidence(item, flags)
        verdict = diagnose(item, evidence)
        if verdict.changes:
            try:
                change_item(item, verdict)
                notify_holders(item, verdict)
            except GateRefused:
                pass                            # the item stands; the refusal is recorded with it
        record_verdict(item, verdict)
        if not room_to_finish_cleanly():
            break

    lint_library()
    record()
    write_gate_state(inventory)
    return finish("ok", "intake closed, items reviewed with verdicts, changes live, holders told")


def orient():
    """Read the state digest, REVIEWED and the campaign record before reading any holder's runs.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers;
    reports handed to the routine arrive with it. REVIEWED says when each item was last reviewed
    and on what evidence."""


def bootstrap():
    """First pass: write the inventory script, seed REVIEWED, then go steady.

    The inventory is deterministic and runs every pass, so it is the routine's own script under a
    stable name. Seed REVIEWED with every item as never reviewed. Then set the phase to steady and
    carry on into a real pass."""


def take_intake():
    """Fix and answer every defect report handed to this routine, in this pass.

    A defect in the routine's remit is fixed and answered with the change; one outside it is
    passed to its owner with the diagnosis; one that must wait is answered with what it waits on.
    A thread the answer ends is closed."""


def run_inventory():
    """Run the inventory script over the whole library; return every item, holders and evidence.

    Holders come from HOLDERS; fresh evidence counts the runs since the item's last review that
    exercised it, the failures that name it and the reports about it. The script's output is the
    basis for selection, so no count here comes from memory."""


def select(inventory):
    """Choose this pass's items: the campaign's next items, then flagged ones, then the stalest.

    Stalest means least-recently-reviewed with fresh evidence that is real use, confirmed by
    finding it in a holder's transcript. An item with no fresh evidence is not reviewed again."""


def triage(selected):
    """Hand every selected item to one read-only child with the criteria; return its flags.

    The child reads the evidence for all candidates at once and flags which ones carry a signal
    worth a full study, so the pass spends its depth where the evidence is."""


def gather_evidence(item, flags):
    """Collect how the holders actually used ONE item, from their transcripts and records.

    Read through the routine's own extraction scripts: where the item was reached, where it
    helped, where it misfired, the corrections it failed to prevent. Evidence names its runs."""


def diagnose(item, evidence):
    """Give ONE item a verdict from its evidence: change it, remove it, or leave it standing.

    A change fixes what the evidence shows, in the item's own terms, without narrowing what its
    other holders rely on. Removal needs a reason beyond disuse: merged into another item, broken
    beyond repair, duplicated. Standing is a real verdict and carries its reason."""


def change_item(item, verdict):
    """Write the change to the shared item under its approval dial; raise GateRefused if refused.

    Build a merge or a transplant with a script rather than by hand; prove every branch of a
    merged item; delete an original only after every holder is migrated to what replaces it and
    every reference to it is settled. The item's own test and the lint run before the change
    counts as live."""


def notify_holders(item, verdict):
    """Migrate or tell every holder the change touches.

    A holder this routine may edit is migrated in the same pass and verified; any other holder
    gets an addressed report naming the change, what it must do and by when."""


def record_verdict(item, verdict):
    """Write the item's verdict to REVIEWED: this pass, the verdict, the change, the runs cited."""


def room_to_finish_cleanly():
    """True while the budget can study, change and verify the next item and still lint and record.

    A boundary check, never a ration: the items left keep their place in the order for the next
    pass."""


def lint_library():
    """Lint the whole library after this pass's changes; fix a problem this pass caused.

    A problem this pass did not cause goes to its owner in a report."""


def record():
    """Append one LEDGER entry, set state/phase.json and keep state/ lean.

    The entry names the reports closed, the items reviewed with their verdicts, the changes made,
    the holders told and the candidates rejected with why; in a campaign, the distance to TARGET.
    Record the phase under the key `phase`: the lifecycle label from PHASES, never the stage the
    run is in — campaign while TARGET is set and not met, steady otherwise. Write every state file
    with the file actions or the routine's own scripts; a write made through a shell command is
    invisible to the checks that read the run's actions. Rotate LEDGER.md in the run its size
    passes the byte cap the recipe names — derived from this routine's own entries, set above the
    size of the tail it keeps with room for several more — and when a rotation leaves the file
    over the cap or within one entry of it, fix the numbers with that measurement. A file under
    state/ is one a later run reads, under a stable name overwritten in place; staged copies and
    census files only this pass needed are deleted before finishing. Machinery that failed or
    misled you and that you worked around goes to its owner with `report`."""
    ledger.append("reports closed, verdicts, changes, holders told, rejected + why")


def write_gate_state(inventory):
    """Write state/gate.json, the run gate's view of the library, from this pass's inventory.

    It holds a fingerprint of the library (the item count, the library's last commit touching it),
    the fresh-evidence count per item at the end of the pass and, in a campaign, the distance to
    TARGET. The gate only reads this file and the run is its only writer."""
