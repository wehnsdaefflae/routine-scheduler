"""General task — the sane default workflow.

Orient, do the instruction's work in verified steps, record, commit. This file is a
PATTERN, not a program: the orchestrator never executes it —
it *acts it out*, one engine action per turn, following the control flow below (its branches,
loops, and error handling). The dummy imports name the parameters this routine works with; the
clarifier pins them down for the concrete task, and `decompose` turns this pattern into the
routine's own markdown state-machine (main.md + steps/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve to anything at run time. Each names one piece of information the
# clarifier must fix for THIS routine — the type, and what it means, live in the comment.
from routine.params import (
    DELIVERABLE,    # str       — the concrete artifact this routine produces, and where it lives
    SOURCES,        # list[str] — the inputs/feeds each run draws from (may be empty)
    SINCE_MARKER,   # str       — how "new since the last run" is tracked (a file under state/)
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, util, write_util, llm, spawn, subruns,
                             wait, ask_user, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "General task",
    "slug": "general-task",
    "description": "The sane default — orient, do the work in verified steps, record, commit.",
    "when_to_use": "Most recurring instructions with no more specific pattern: collect / produce "
                   "/ maintain something on a schedule, tend a long-running goal, run a periodic "
                   "check. Use it when the instruction says WHAT to deliver and the HOW is "
                   "ordinary tool work.",
    "version": 12,
    "tags": ["general", "research", "tool-use"],
    "includes": ["ask-policy", "web-research", "decision-record"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "steady", "wrap-up"]     # tracked in state/phase.json


class NeedsDecision(Exception):
    """A choice only the user can make — raised to file a deferred question and carry on."""


class ExternalBlocker(Exception):
    """This item can't proceed right now (a source is down, an answer is pending)."""


def main():
    """One run of the routine — the top-level control flow."""
    orient()                                    # consume the state digest before anything new

    if phase.current() == "wrap-up":
        return wrap_up()                        # terminal: the goal is reached — verify once, hand over, stop

    if phase.current() == "bootstrap":
        bootstrap()                             # first run(s): set up state/, then carry on
        # fall through — a bootstrap run still delivers a first real increment, so the routine
        # is useful after its FIRST fire rather than after its second.

    work = pick_work()                          # what THIS run delivers (finish in-progress work first)
    if not work:
        # ESTABLISH it, don't assume it: the sources were checked and came back empty. Say that
        # plainly and finish — an idle run reported honestly beats an invented increment.
        return finish("ok", "Nothing due this run; sources checked, standing obligations guarded.")

    if separable(work):
        # Separable bulk work → parallel children, each with a self-contained prompt + disjoint
        # outputs. Keep working, then fold in their results.
        for chunk in batches(work):
            spawn(chunk)
        collect_children()
    else:
        for item in work:
            try:
                verify(execute(item))           # do it, then read it back — never assume
            except NeedsDecision as decision:
                ask_user(decision, mode="deferred")   # → Decisions page; this item waits for the answer
            except ExternalBlocker:
                continue                        # can't proceed now; move to the next item

    recheck()                                   # more is often due once this pass lands — look before finishing
    record()                                    # update state/phase.json + append the LEDGER entry
    return finish("ok", "what was delivered, decisions taken, open ends")


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, user messages/answers) before
    exploring anything new — so you never re-try a known dead end. The digest already carries the
    LEDGER tail and says when there is more; read the file only if it says so."""


def bootstrap():
    """First run(s): create state/, understand the instruction's domain, and file deferred
    questions for genuinely pivotal unknowns (ask-policy). Advance state/phase.json to 'steady'
    once the basic loop can run, then continue into this run's normal work — a first fire that
    delivers nothing but setup costs the user a whole cadence."""


def pick_work():
    """From the instruction, the current phase, and any user messages, decide what this run
    delivers. Prefer finishing in-progress work; guard standing obligations first. Draw new items
    from SOURCES since SINCE_MARKER.

    Take everything that is genuinely due — this is a work LIST, not a token gesture. What bounds
    a run is the stopping conditions in `state/stopping.json` (the user's own words for what DONE
    means, inlined above and accounted for in your finish summary). The turn budget is a runaway
    BACKSTOP, not a ration: do not stop early because turns are being spent, and do not stretch a
    finished job to fill them."""


def recheck():
    """Before finishing, ask ONCE MORE whether more is due. The pass you just completed is the
    commonest thing that reveals it: a source paginated, a fixed item unblocked the next one, an
    answer arrived mid-run, a delivery exposed the gap behind it.

    Re-run pick_work. If it comes back with items and the remaining budget can still deliver AND
    verify them cleanly, do them now — then ask again. Finishing with known-due work left on the
    table because the first pass already felt like 'a run's worth' is exactly what this step
    exists to prevent.

    Stop when pick_work comes back empty, or at a clean boundary when the budget can no longer
    finish and verify the next item — never half-built. That boundary is not a ration: the turn
    budget is a runaway BACKSTOP, so stopping is a judgement about finishing what you start, not
    about spending turns."""


def separable(work):
    """True when the work splits into independent chunks whose own context or budget would crowd
    out the rest of this run if done inline. Judge the shape of the work, not its length."""


def execute(item):
    """Do the next piece of the work and return its result.

    Code runs through a CAPABILITY, never ad hoc: take whichever your CAPABILITIES list offers.
    A judgment-free step you repeat identically every run belongs in this routine's own
    persistent tooling — written once, called thereafter — while a capability other routines
    would share too belongs in the shared library, authored with a selftest before first use.
    Read/write files with read_file/write_file; verify external facts by searching, not from
    memory (web-research); use `llm` for a scoped one-shot judgment."""


def verify(result):
    """Confirm what was produced — read it back, check the exit code, count the results, see the
    test pass. A claimed-but-unverified outcome is the worst failure this system knows."""


def batches(work):
    """Split large work into disjoint chunks for parallel sub-workflows (one prompt each)."""


def collect_children():
    """Watch the children through to their exits and fold in what they hand back.

    `subruns` gives their status table; `wait` blocks until the next one finishes. Every child
    hands back a summary, plus any files it wrote into its own artifacts/ — the engine copies
    those to artifacts/from-sub-<n>/ and NAMES them in the one CHILD RUN FINISHED notification.
    Read what you need from there before finishing; a child's context is gone once it exits."""


def record():
    """Update state/phase.json and any state files; append exactly one LEDGER entry for the run
    (what changed, why, decisions, and candidates rejected + why). Advance phase.json to
    'wrap-up' once the GOAL-scoped stopping conditions in `state/stopping.json` are met — the
    user's own words for the state after which this ROUTINE is finished — so the next fire closes
    the job out instead of looking for more. Then sweep the run once for
    machinery friction you merely worked around — an action or tool that failed or misled you, a
    consent flow that asked for too much or too little — and file each real hitch with the
    `report` action before finishing (leave `target` unset if you cannot name the owner; triage
    routes it). A finish summary is read as the task's outcome, not as a defect stream.
    When LEDGER.md grows past ~400 lines or ~40 entries, rotate it THAT run as a required
    part of recording (not deferrable housekeeping): archive the older entries with a
    one-line rollup note pointing at the archive, keeping only the recent tail — an
    unbounded LEDGER is its own defect."""
    ledger.append("what changed, why, decisions, rejected candidates")


def wrap_up():
    """Terminal phase — the GOAL-scoped stopping conditions are met and this is the closing run.
    Do three things and nothing else: VERIFY the DELIVERABLE one final time against the primary
    source (never against your own state files), TELL the user in plain words where it lives and
    how to reach it, and FINISH accounting those conditions as met. Start no new work, draw
    nothing new from SOURCES, and open no new question."""
    return finish("ok", "Goal reached: deliverable verified, and where it lives.")


if __name__ == "__main__":
    main()
