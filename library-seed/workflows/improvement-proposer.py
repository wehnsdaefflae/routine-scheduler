"""Recurring improvement-proposer — research a product along fixed axes, file decidable proposals.

Each run this routine studies how a target system (here, the routine-scheduler itself) could be
improved, iterating a FIXED set of axes (technology, methodology, functionality, aesthetics). For
each axis it grounds research in BOTH the current system (its own code, docs, and behaviour) AND
external prior art, then records every genuinely-new, well-grounded proposal as a deferred
accept/reject decision item on the Decisions subpage — mirrored to a human-readable decisions.md.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*, one
engine action per turn, following the control flow below. The routine IMPLEMENTS NOTHING: it
researches, judges and writes proposals. Whether to act on a proposal is the user's decision, made
on the subpage. The dummy imports name the parameters the clarifier pins down for the concrete
instruction.

A proposal the user ACCEPTS is HANDED OVER to the routine that builds, and that hand-off is part
of the record rather than an exception to it. The acceptance reaches this routine and no other, so
a proposer that does not pass it on is a proposer whose accepts are indistinguishable from
rejects: measured on the reference instance 2026-09-22, roughly twenty proposals accepted over two
months had reached no builder; one that had been built by hand was later retired as unused
because nothing recorded that somebody had asked for it. A backlog whose accepts change nothing is
a backlog that costs a run a week and returns nothing.
"""

# --- Parameter contract -------------------------------------------------------------------------
# None of these resolve at run time. Each names one piece of information the clarifier fixes for
# THIS routine; the comment gives its type and meaning.
from routine.params import (
    SUBJECT,          # str       — the system under study and where its parts live (daemon, workflow/trait lib, dashboard/UI, utils)
    AXES,             # list[str] — the fixed axes reviewed EVERY run, in order (e.g. technology, methodology, functionality, aesthetics)
    PROPOSED_LEDGER,  # str       — state file tracking already-recorded proposals (title + axis), the dedupe memory
    DECISIONS_MIRROR, # str       — the durable human-readable decisions.md in the working dir that mirrors the subpage
    BUILDER,          # str       — the routine that IMPLEMENTS an accepted proposal (its slug), or "" if none exists
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
    "version": 6,
    "tags": ["research", "proposals", "decision-support", "recurring"],
    "includes": ["ask-policy", "web-research", "decision-record", "problem-routing"],
    # It researches, judges, writes and asks. No children, no authoring, no implementation.
    # The hand-off of an accepted proposal rides the report channel, which every routine holds.
    "tools": ["read_file", "write_file", "util", "llm", "ask_user", "finish"],
}

PHASES = ["steady"]     # no cross-run milestones — every run is the same review loop


class NothingNew(Exception):
    """An axis produced no genuinely-new, well-grounded proposal this run — noted, not filed."""


def main():
    """One run: review every fixed axis, record every new proposal, and report per-axis outcomes."""
    orient()                                    # consume state digest + prior proposals before researching anything

    handed = hand_off_accepted()                # accepted proposals -> the BUILDER, before any new research

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

    mirror_and_record(outcomes, handed)         # sync decisions.md, update PROPOSED_LEDGER, append LEDGER

    if not handed and all(filed == ["nothing new"] for filed in outcomes.values()):
        # Every axis was researched, none produced anything materially new, and nothing was
        # waiting to be handed over. That is a real outcome, established by the review — say it
        # plainly and finish rather than padding the subpage with restatements to have filed
        # something.
        return finish("ok", "Every axis reviewed; nothing materially new to file this run.")

    return finish("ok", summarize(outcomes, handed))   # what was filed, what was handed over, empty axes


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, user messages) and read
    PROPOSED_LEDGER before exploring anything new — so you never re-propose a known idea or chase
    a dead end. The digest already carries the LEDGER tail and says when there is more; read the
    file only if it says so."""
    read_file(PROPOSED_LEDGER)


def hand_off_accepted():
    """Hand every newly-ACCEPTED proposal to BUILDER, before researching anything new.

    An acceptance arrives as an answer to the deferred question this routine filed, and it lands
    here and nowhere else — no other routine is told, and no index turns an answered question into
    work. So this step is the whole difference between a backlog and a queue, and it comes FIRST
    because a run that spends its budget on research and then runs out has silently chosen new
    proposals over decided ones.

    For each acceptance (and each proposal DECISIONS_MIRROR already records as accepted but never
    handed over), file ONE report to BUILDER carrying:
      • a title marked as DECIDED work, so the receiver's queue can tell authorization from
        suggestion at a glance, followed by the proposal's own title verbatim;
      • the axis, the user's own words if the answer carried any, and the rationale condensed to
        what a builder needs — the evidence, the parts of the system involved, the prior art;
      • a FIRST INCREMENT: the smallest piece worth shipping on its own. For anything whose rough
        effort was "large", this line is what makes the hand-off usable at all.
      • a plain statement that this is decided work, and that this routine implements nothing.

    PACE IT TO THE ENGINE'S OPEN-THREAD CAP, oldest acceptance first. The cap refuses a further
    report to one owner and names the ids already open; that refusal is correct here, because a
    builder ships one or two items a run and a queue longer than that is a backlog again. Stop at
    the refusal, record the remainder as accepted-and-awaiting-hand-off, and send them next run as
    the earlier threads close. Never open a parallel thread around the cap and never drop a
    proposal for not fitting this run.

    With BUILDER empty there is no builder on this instance, which is a finding rather than a
    no-op: say so in the run report, once, naming how many accepted proposals are waiting.

    Return the (title, report id) pairs handed over, plus the titles held back and why.
    """


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


def mirror_and_record(outcomes, handed):
    """Mirror every newly-filed proposal into the durable, human-readable DECISIONS_MIRROR
    (decisions.md) so the backlog survives outside the subpage, append the new (title, axis) rows
    to PROPOSED_LEDGER, then write exactly one LEDGER entry: axes reviewed, proposals filed,
    candidates rejected as not-new + why, and what was handed to the builder.

    RECORD EVERY HAND-OFF AGAINST ITS OWN PROPOSAL in DECISIONS_MIRROR — the status line becomes
    "accepted -> handed off <date> as <report id>", or "accepted -- awaiting hand-off" with the
    ids the cap named. This is the one amendment to an existing entry the mirror allows, because
    it records what BECAME of an item rather than restating it, and without it the next run
    cannot tell an accept that reached a builder from one that did not. Rotate LEDGER.md THAT run, as a required part of
    recording rather than deferrable housekeeping, whenever it exceeds the SIZE IN BYTES the
    recipe names: archive the older entries with a one-line rollup note pointing at the
    archive, keeping only the recent tail — an unbounded LEDGER is its own defect.
    MEASURE THE THRESHOLD IN BYTES, AND DERIVE IT FROM THIS ROUTINE'S OWN ENTRIES. A
    proposal ledger's entries are narratives, not one-liners, so a count of lines or entries
    stops tracking what a reader pays: the routine built from this pattern was measured at
    132,453 bytes across 23 entries on 2026-09-21 — comfortably inside its own "~40 entries"
    trigger while being three times the size that trigger exists to prevent.
    THE CAP AND THE KEPT TAIL ARE ONE PAIR, AND A CROSSED PAIR IS WORSE THAN NO TRIGGER.
    The cap is a byte CEILING; keeping the last N entries is a count FLOOR worth N x the mean
    entry size, and the larger of the two is the one that actually binds. Setting the cap at
    "about N entries" makes them equal by construction, and the trigger is then inert either
    way: at or just above the floor it rotates one entry, lands the file just under, and
    re-trips on the very next append -- a rotation every single run forever, the live file
    never holding more than the floor; below the floor it cannot be satisfied at all while
    keeping N entries, so a correct run's only option is to skip it. Both failure modes were
    live on that same instance on 2026-09-21: five recipes paired a byte cap with a kept tail
    and four were already crossed, one self-defeating by a single kilobyte, while a sixth sat
    at 2.8x a cap no run had ever been able to honour. So set the cap ABOVE the floor with real
    headroom -- the mean of the last three entries, times the tail you keep, plus room for
    several more runs -- and if a rotation leaves the file still over the cap, OR lands it
    within one entry's size of the cap, the numbers are wrong: fix them with the measurement
    that justifies it, raising the ceiling or lowering the floor, but never leaving them
    crossed. A threshold you trip by complying with it is one every run learns to ignore."""
    write_file(DECISIONS_MIRROR, "append each new decision item; stamp each hand-off on its own")
    write_file(PROPOSED_LEDGER, "append (title, axis) for each filed proposal")
    ledger.append("axes reviewed, proposals filed per axis, empty axes, near-dupes rejected, "
                  "accepted proposals handed to the builder, and those still awaiting hand-off")


def summarize(outcomes, handed):
    """Build the run report: what was handed to the builder (title -> report id) and what is still
    awaiting hand-off, then for each of the fixed axes either the titles filed or an explicit
    'nothing new' — so completion (every axis reviewed) is visible at a glance. The hand-offs lead
    because they are the only part of the run that changes anything outside this routine."""


if __name__ == "__main__":
    main()
