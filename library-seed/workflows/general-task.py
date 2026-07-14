"""General task — the sane default workflow.

Orient, do the instruction's work in small verified steps, record, commit. This file is a
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
    DONE_WHEN,      # str       — the overall completion criterion, or "" if this is open-ended
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "General task",
    "slug": "general-task",
    "description": "The sane default — orient, do the work in small verified steps, "
                   "record, commit.",
    "when_to_use": "Most recurring instructions with no more specific pattern: collect / produce "
                   "/ maintain something on a schedule, tend a long-running goal, run a periodic "
                   "check. Use it when the instruction says WHAT to deliver and the HOW is "
                   "ordinary tool work.",
    "version": 8,
    "tags": ["general", "research", "tool-use"],
    "includes": ["ask-policy", "global-utils", "web-research", "ledger-discipline"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "steady", "wrap-up"]     # tracked in state/phase.json
COMPLETION = (
    "per run: a concrete increment, a LEDGER entry, everything committed; "
    "overall: DONE_WHEN is met and the user has been told where the deliverable lives"
)

PARALLEL_THRESHOLD = 8      # delegate to sub-workflows only when the work is genuinely large


class NeedsDecision(Exception):
    """A choice only the user can make — raised to file a deferred question and carry on."""


class ExternalBlocker(Exception):
    """This item can't proceed right now (a source is down, an answer is pending)."""


def main():
    """One run of the routine — the top-level control flow."""
    orient()                                    # consume the state digest + LEDGER before anything new

    if phase.current() == "bootstrap":
        bootstrap()                             # first run(s): set up state/, first honest increment
        return finish("ok", "Bootstrapped; advanced to steady.")

    work = pick_work()                          # what THIS run delivers (finish in-progress work first)
    if not work:
        return finish("ok", "Nothing due this run; standing obligations guarded.")

    if len(work) > PARALLEL_THRESHOLD:
        # Separable bulk work → parallel children, each with a self-contained prompt + disjoint
        # outputs. Keep working, then fold in their results.
        children = [spawn(chunk) for chunk in batches(work)]
        while children:
            children = wait(children)           # blocks until the next child finishes; returns the rest
    else:
        for item in work:
            try:
                verify(execute(item))           # one small step, then read it back — never assume
            except NeedsDecision as decision:
                ask_user(decision, mode="deferred")   # → Decisions page; this item waits for the answer
            except ExternalBlocker:
                continue                        # can't proceed now; move to the next item

    record()                                    # update state/phase.json + append the LEDGER entry
    return finish("ok", "what was delivered, decisions taken, open ends")


def orient():
    """Read the state digest (phase, last result, LEDGER tail, user messages/answers) and
    LEDGER.md before exploring anything new — so you never re-try a known dead end."""
    read_file("LEDGER.md")


def bootstrap():
    """First run(s): create state/, understand the instruction's domain, file deferred questions
    for genuinely pivotal unknowns (ask-policy), and produce a first honest increment of
    DELIVERABLE. Advance state/phase.json to 'steady' once the basic loop has produced output."""


def pick_work():
    """From the instruction, the current phase, and any user messages, decide what this run
    delivers. Prefer finishing in-progress work; guard standing obligations first. Draw new items
    from SOURCES since SINCE_MARKER."""


def execute(item):
    """Do one small step and return its result. There is NO shell — run code only via `util`; if
    nothing fits, `write_util` a selftested PEP-723 script (it may call sibling utils), then call
    it. Read/write files with read_file/write_file; verify external facts by searching, not from
    memory (web-research); use `llm` for a scoped one-shot judgment."""


def verify(result):
    """Confirm what was produced — read it back, check the util's exit code, count the results.
    A claimed-but-unverified outcome is the worst failure this system knows."""


def batches(work):
    """Split large work into disjoint chunks for parallel sub-workflows (one prompt each)."""


def record():
    """Update state/phase.json and any state files; append exactly one LEDGER entry for the run
    (what changed, why, decisions, and candidates rejected + why)."""
    ledger.append("what changed, why, decisions, rejected candidates")



if __name__ == "__main__":
    main()
