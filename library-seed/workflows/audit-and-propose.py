"""Audit and propose — examine what this routine does not own; turn findings into decisions.

The mechanism: a SUBJECT the routine reads but never changes (configuration surfaces, a product
reviewed along fixed axes, the fleet's usage measured by an experiment) is examined every run
through a LENS: criteria for ideal values, fixed axes checked against the product and external
prior art, or a bounded experiment against a baseline. Every well-grounded new finding becomes ONE
decidable proposal for its OWNER — a Decisions item carrying the exact change, or an addressed
report — and the routine applies nothing itself: its product is the REPORT and the decisions.

Two practices carry the mechanism. A proposal the principal accepts is handed to the routine that
builds it before any new research starts, because the acceptance lands here and nowhere else: a
proposer that does not pass it on leaves an accept indistinguishable from a reject. Every proposal
quotes the live value it rests on, re-read at the source right before filing, because a premise
recalled instead of read is how a wrong change gets approved.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- read access to the subject: the configuration files and the scheduler's read API, the
  product's repository and documentation, the usage records;
- web research for prior art;
- a model endpoint where the lens is an experiment;
- deferred questions and addressed reports. It needs no authoring capability: the owner applies
  the change.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    SUBJECT,     # str   — what is examined and where each part of it is read live (config surfaces, a product's code and docs, usage records)
    LENS,        # dict  — how it is examined: criteria for ideal values, the fixed axes reviewed every run, or an experiment method with its baseline
    OWNER,       # dict  — who applies an accepted proposal: the builder routine a decided proposal is handed to, or the principal through a one-click change
    PROPOSALS,   # str   — the dedupe record of every proposal filed, with its status (open, accepted and handed off with the report id, rejected) and its follow-up date
    REPORT,      # str   — the document each run rewrites, flagged findings first
    BASELINE,    # str   — the fingerprints of the subject as this run read it, written for the run gate
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, edit_file, util, script, llm, spawn, wait,
                             memory_read, memory_write, ask_user, report, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Audit and propose",
    "slug": "audit-and-propose",
    "description": "Examine a subject this routine does not own (configuration, a product along "
                   "fixed axes, usage measured by an experiment), hand accepted proposals to their "
                   "builder first, file every new well-grounded finding as one decidable proposal "
                   "quoting the live value it rests on, rewrite the report, apply nothing.",
    "when_to_use": "A recurring review whose product is decisions for someone else: audit the "
                   "instance's configuration and propose one-click changes, research how a "
                   "product could improve along fixed axes and file proposals, run bounded "
                   "experiments and report confirmed wins to the routine that owns the surface. "
                   "The routine never changes the subject itself. Not for a routine that also "
                   "applies the change (maintain-a-codebase, curate-a-library).",
    "version": 1,
    "tags": ["audit", "proposals", "research", "decisions", "review"],
    "includes": ["audit-coverage", "verify-independently"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap — the first run: find where each part of SUBJECT lives, record that map, write a
#               first full report; then steady.
#   steady    — a full examination every run. Skip-eligible: the gate may skip a fire when the
#               subject matches BASELINE, no answer is pending and no open proposal is past its
#               follow-up date.
PHASES = ["bootstrap", "steady"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · hand_off_accepted — every proposal accepted since the last run is handed to its OWNER as "
    "decided work, with the report id recorded against it in PROPOSALS",
    "d2 · read_subject — every part of SUBJECT was read live this run",
    "d3 · examine — every part or axis has a verdict: flagged with its evidence, or nothing new "
    "with what was checked",
    "d4 · file_proposal — every new finding is filed once as a decidable proposal quoting the "
    "value read at its source this run",
    "d5 · follow_up — every open proposal past its follow-up date is raised again with what "
    "changed since",
    "d6 · write_report — REPORT is rewritten from this run's reading, flagged findings first",
    "d7 · record — PROPOSALS, state/phase.json and BASELINE are current",
]


class PremiseGone(Exception):
    """The value a finding rests on moved at the source; against the value as it stands now the
    finding does not hold, so it is not filed."""


def main():
    """One run: hand accepted proposals to their owner first, read the subject live, examine it
    through the lens, file what is new, follow up what is overdue, rewrite the report, record."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through: the first run examines the whole subject.

    hand_off_accepted()                         # FIRST: an acceptance lands here and nowhere else
    reading = read_subject()                    # live, every part, never from memory
    findings = examine(reading)

    for finding in new_findings(findings):
        try:
            verify_premise(finding)             # re-read at the source right before filing
            file_proposal(finding)
        except PremiseGone:
            continue                            # it does not hold against the live value

    follow_up()
    write_report(findings)
    record(reading)
    return finish("ok", "hand-offs, per part or axis: filed or nothing new, open proposals")


def orient():
    """Read the state digest, PROPOSALS and the map of SUBJECT's parts before reading any part.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers.
    PROPOSALS is the memory that keeps runs additive: an idea already filed, accepted or rejected
    is not filed again."""


def bootstrap():
    """First run: find where each part of SUBJECT lives, record the map, then go steady.

    Discover the locations by inspecting the deployment and record each one with what it holds,
    so later runs confirm the map instead of rediscovering it. Then set the phase to steady and
    carry on into a full examination."""


def hand_off_accepted():
    """Hand every proposal accepted since the last run to its OWNER, before any new research.

    For each acceptance (and each one PROPOSALS records as accepted but not yet handed off), file
    one addressed report to the builder: a title marking it as decided work followed by the
    proposal's own title, the principal's words if his answer carried any, the evidence and the
    parts involved, and the first increment worth shipping on its own. Pace the hand-offs to the
    owner's open-thread cap, oldest first: when the cap refuses, record the rest as accepted and
    waiting (with the open ids it named) and send them as threads close. Record every hand-off
    against its proposal in PROPOSALS. An answer that matches no filed question is recorded as
    such and passed to the principal, not guessed at."""


def read_subject():
    """Read every part of SUBJECT live this run and return the reading with its fingerprints.

    Read each value at its source (the file, the API, the repository), never from memory or an
    earlier report. A part that could not be read is named as unread; its verdict is unknown."""


def examine(reading):
    """Examine every part or axis through the LENS and return a verdict for each.

    Criteria: each value judged against the ideal for this deployment, flagged with the generally
    sound value and the case-specific one when they differ. Axes: each axis reviewed against the
    product and against prior art found by research, with "nothing new" as an explicit verdict.
    Experiment: one bounded experiment against the baseline, its numbers from real output and a
    negative result recorded as a result. A verdict names what it rests on."""


def new_findings(findings):
    """The flagged findings not already in PROPOSALS, strongest first; a rephrase is not new."""


def verify_premise(finding):
    """Re-read the value a finding rests on at its source, right before filing it.

    A proposal quotes the value as it stands now. When the value moved since the examination read
    it, re-judge the finding against the new value: it is filed quoting the new value if it still
    holds; PremiseGone is raised if it does not."""


def file_proposal(finding):
    """File ONE finding as a decidable proposal for its OWNER, then record it in PROPOSALS.

    For the principal: a deferred question carrying the exact change (the value now, the value
    proposed, where it lives, why) so one click decides it. For a builder routine: an addressed
    report with the evidence. Every materially new finding is filed; ordering is for the reader,
    not a quota."""


def follow_up():
    """Raise once more every open proposal past its follow-up date, with what changed since.

    A proposal that went unanswered is raised again, not duplicated; its follow-up date moves."""


def write_report(findings):
    """Rewrite REPORT from this run's reading: flagged findings first, then every part or axis.

    Each entry names the current value or state, the verdict, the recommendation and a one-line
    reason. The map of where each part lives is part of the report, so a later run reuses it."""


def record(reading):
    """Update PROPOSALS and BASELINE, append one LEDGER entry, set the phase, keep state/ lean.

    The entry names the hand-offs, the parts or axes examined, the proposals filed, the follow-ups
    and the candidates rejected with why. BASELINE gets the fingerprint of every part as this run
    read it, for the run gate; the gate only reads it. Record the phase under the key `phase`: the
    lifecycle label from PHASES, never the stage the run is in. Write every state file with the
    file actions or the routine's own scripts; a write made through a shell command is invisible
    to the checks that read the run's actions. Rotate LEDGER.md in the run its size passes the byte
    cap the recipe names — derived from this routine's own entries, set above the size of the tail
    it keeps with room for several more — and when a rotation leaves the file over the cap or
    within one entry of it, fix the numbers with that measurement. A file under state/ is one a
    later run reads, under a stable name overwritten in place; anything only this run needed is
    deleted before finishing. Machinery that failed or misled you and that you worked around goes
    to its owner with `report`."""
    ledger.append("hand-offs, examined, filed, followed up, rejected + why")
