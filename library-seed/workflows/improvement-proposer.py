"""Recurring improvement-proposer — research a product along fixed axes, file decidable proposals.

Each run this routine studies how a target system (here, the routine-scheduler itself) could be
improved, iterating a FIXED set of axes (technology, methodology, functionality, aesthetics). For
each axis it grounds research in BOTH the current system (its own code, docs, and behaviour) AND
external prior art, then records every genuinely-new, well-grounded proposal as a deferred
accept/reject decision item on the Decisions subpage — mirrored to a human-readable decisions.md.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*, one
engine action per turn, following the control flow below. The routine is RECORD-ONLY: it researches
and writes proposals; it performs NO implementation and takes no other outward action. Whether to
act on a proposal is the user's decision, made on the subpage. The dummy imports name the
parameters the clarifier pins down for the concrete instruction.
"""

# --- Parameter contract -------------------------------------------------------------------------
# None of these resolve at run time. Each names one piece of information the clarifier fixes for
# THIS routine; the comment gives its type and meaning.
from routine.params import (
    SUBJECT,          # str       — the system under study and where its parts live (daemon, workflow/trait lib, dashboard/UI, utils)
    AXES,             # list[str] — the fixed axes reviewed EVERY run, in order (e.g. technology, methodology, functionality, aesthetics)
    PROPOSED_LEDGER,  # str       — state file tracking already-recorded proposals (title + axis), the dedupe memory
    DECISIONS_MIRROR, # str       — the durable human-readable decisions.md in the working dir that mirrors the subpage
)

from routine.actions import read_file, write_file, util, llm, ask_user, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Improvement proposer",
    "slug": "improvement-proposer",
    "description": "Each run, research how a subject could be improved along fixed axes and file "
                   "every materially-new idea as a deferred accept/reject decision item — record-only.",
    "when_to_use": "Recurring 'keep finding ways to improve X and let me decide' instructions: the "
                   "routine researches (self + external prior art) along a fixed set of axes every "
                   "run, records decidable proposals to a Decisions subpage, and never implements "
                   "anything. Use when the deliverable is a growing, deduplicated backlog of "
                   "user-decidable proposals, not the changes themselves.",
    "version": 3,
    "tags": ["research", "proposals", "decision-support", "record-only", "recurring"],
    "includes": ["ask-policy", "web-research", "decision-record"],
    # Record-only: it researches, judges, writes and asks. No children, no authoring, no
    # outward act but the deferred question itself.
    "tools": ["read_file", "write_file", "util", "llm", "ask_user", "finish"],
}

PHASES = ["steady"]     # no cross-run milestones — every run is the same review loop


class NothingNew(Exception):
    """An axis produced no genuinely-new, well-grounded proposal this run — noted, not filed."""


def main():
    """One run: review every fixed axis, record every new proposal, and report per-axis outcomes."""
    orient()                                    # consume state digest + prior proposals before researching anything

    already = load_recorded()                   # {(title, axis)} + gist of what's already on the subpage
    outcomes = {}                               # axis -> ["filed: <title>", ...] or ["nothing new"]

    for axis in AXES:                           # the four fixed axes, EVERY run, in order
        try:
            candidates = research_axis(axis)    # concrete, well-grounded proposals grounded in self + external prior art
            fresh = [c for c in candidates if is_new(c, already)]   # diff against PROPOSED_LEDGER / subpage
            if not fresh:
                raise NothingNew
            outcomes[axis] = [record_proposal(c, already) for c in rank(fresh)]
        except NothingNew:
            outcomes[axis] = ["nothing new"]

    mirror_and_record(outcomes)                 # sync decisions.md, update PROPOSED_LEDGER, append LEDGER

    if all(filed == ["nothing new"] for filed in outcomes.values()):
        # Every axis was researched and none produced anything materially new. That is a real
        # outcome, established by the review — say it plainly and finish rather than padding the
        # subpage with restatements to have filed something.
        return finish("ok", "Every axis reviewed; nothing materially new to file this run.")

    return finish("ok", summarize(outcomes))    # per-axis: what was filed, and where an axis was empty


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, user messages) and read
    PROPOSED_LEDGER before exploring anything new — so you never re-propose a known idea or chase
    a dead end. The digest already carries the LEDGER tail and says when there is more; read the
    file only if it says so."""
    read_file(PROPOSED_LEDGER)


def load_recorded():
    """Parse PROPOSED_LEDGER into the set of (title, axis) already recorded, plus enough of each
    item's gist to catch near-duplicates. This is the memory that keeps runs additive."""


def research_axis(axis):
    """For ONE axis, surface concrete improvement proposals grounded in BOTH sources:
      • the current SUBJECT itself — read its daemon, workflow/trait library, dashboard/UI, and
        utils (read_file/util) to find real gaps, frictions, and weak spots on this axis;
      • external prior art — search for tools, techniques, and patterns worth adapting
        (web-research), verifying claims rather than trusting memory.
    Return well-grounded candidates, each with a rationale, expected impact, and rough effort.
    The bar is DEFENSIBILITY, not count: a shallow idea is not a cheaper proposal, it is a
    non-proposal, and dropping it costs the user nothing. Return every candidate that clears the
    bar — the axis is done when the axis is exhausted, not when a quota is filled."""


def is_new(candidate, already):
    """True only if this proposal is materially new: not present in `already` by title+axis, and
    not a restatement of an existing item. A materially-updated version of an old idea counts as
    new (it supersedes, with a note); a rephrase does not."""


def rank(fresh):
    """Order the fresh candidates strongest first (grounding × expected impact ÷ effort, judgement
    via `llm` when it helps). ORDERING ONLY — every materially-new candidate is filed.

    Do not cap this list. The research is already done and the dedupe is already done, so a
    discarded proposal is work the run has ALREADY paid for, thrown away only to be re-derived on
    some later run. Digestibility belongs to the reading surface — the Decisions subpage sorts,
    filters and is read at the user's pace — and pre-empting it here loses ideas instead of
    presenting them well."""


def record_proposal(candidate, already):
    """File ONE proposal as a deferred accept/reject question on the Decisions subpage — the item
    the user edits to open/accepted/rejected. Carry the full record: title, axis, rationale,
    expected impact, rough effort, status=open. This is the routine's only outward action, and it
    is a *question*, never an implementation."""
    ask_user(candidate, mode="deferred")        # → Decisions subpage; the user decides later
    already.add((candidate.title, candidate.axis))
    return f"filed: {candidate.title}"


def mirror_and_record(outcomes):
    """Mirror every newly-filed proposal into the durable, human-readable DECISIONS_MIRROR
    (decisions.md) so the backlog survives outside the subpage, append the new (title, axis) rows
    to PROPOSED_LEDGER, then write exactly one LEDGER entry: axes reviewed, proposals filed,
    candidates rejected as not-new + why. When LEDGER.md grows past ~400 lines or ~40
    entries, rotate it THAT run as a required part of recording (not deferrable
    housekeeping): archive the older entries with a one-line rollup note pointing at the
    archive, keeping only the recent tail — an unbounded LEDGER is its own defect."""
    write_file(DECISIONS_MIRROR, "append each new decision item, matching the subpage")
    write_file(PROPOSED_LEDGER, "append (title, axis) for each filed proposal")
    ledger.append("axes reviewed, proposals filed per axis, empty axes, near-dupes rejected")


def summarize(outcomes):
    """Build the run report: for each of the fixed axes, either the titles filed or an explicit
    'nothing new' — so completion (every axis reviewed) is visible at a glance."""


if __name__ == "__main__":
    main()
