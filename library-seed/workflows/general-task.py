"""General task — the sane default workflow.

Orient, do the instruction's work in verified steps, keep the state the next run reads. This
file is a PATTERN, not a program: the orchestrator never executes it —
it *acts it out*, one engine action per turn, following the control flow below (its branches,
loops, and error handling). The dummy imports name the parameters this routine works with; the
clarifier pins them down for the concrete task, and `decompose` turns this pattern into the
routine's own markdown state-machine (main.md + stages/).
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
from routine.state import phase    # the cross-run lifecycle phase (state/phase.json)

META = {
    "name": "General task",
    "slug": "general-task",
    "description": "The sane default — orient, do the work in verified steps, keep the state "
                   "the next run reads.",
    "when_to_use": "Most recurring instructions with no more specific pattern: collect / produce "
                   "/ maintain something on a schedule, tend a long-running goal, run a periodic "
                   "check. Use it when the instruction says WHAT to deliver and the HOW is "
                   "ordinary tool work.",
    "version": 20,
    "tags": ["general", "research", "tool-use"],
    "includes": ["ask-policy", "web-research", "decision-record"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "steady", "wrap-up"]     # the cross-run lifecycle, in state/phase.json

# What one finished run leaves behind — accounted line by line at every finish.
DONE_WHEN = [
    "d1 · pick_work — everything due this run is taken on, or recorded with why it waits",
    "d2 · verify — every piece delivered was read back or checked and says what the check "
    "showed",
    "d3 · recheck — the sources were asked once more before finishing and what they showed is "
    "recorded",
]


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
    own_tooling()                               # a helper this run wrote is indexed in the recipe
    update_state()                              # what a later run reads; phase only at a milestone
    return finish("ok", "what was delivered, decisions taken, open ends")


def orient():
    """Consume the state digest (phase, last result, user messages/answers) before exploring
    anything new — so you never re-try a known dead end. It already carries what the last run
    left; never re-read the run's history to orient."""


def own_tooling():
    """A routine that has written helper scripts for itself INDEXES them in its own recipe.

    A routine accumulates its own programs over months, and the recipe that commissioned them
    routinely never names a single one. The next run then has a bare directory listing and a
    guess, and the guess it makes is to call the script the way it calls everything else — as a
    shared capability, by the filename it can see. That resolves to nothing: a routine's own
    helper is reached through a different door than the shared library's, and the two doors do
    not fall back to one another. The run loses the turn, and because the name was RIGHT and only
    the door was wrong, the error reads as a missing tool rather than as a recipe that never
    said.

    Measured 2026-09-23 across one instance: three routines with 4, 10 and 33 helper scripts
    each, none of them named anywhere in their recipes, each with a run that reached for one as a
    shared capability and got nothing back. One of those was a first run, so the recipe was as
    new as the scripts.

    So the recipe carries a short index: for each helper, how it is CALLED and one line on what
    it does — and, where the helpers form a sequence, the order they belong in, because that
    ordering is usually hard-won knowledge that exists nowhere else. Past a handful, index the
    directory rather than each file and let each helper's own docstring carry its arguments; a
    copied argument list rots while the code moves. The index is not documentation for a reader.
    It is the difference between a capability this routine already paid to build and one every
    run rediscovers.

    A call that comes back "missing" is a defect in this index, not something to retry. Fix the
    reference in the same run that hit it.

    THE SAME APPLIES TO A SHARED CAPABILITY WHOSE CALL SHAPE A RUN KEEPS GETTING WRONG — and the
    place the shape has to live is the STEP THAT MAKES THE CALL, not a note the routine wrote to
    itself. A recipe step that says WHAT to fetch and never HOW to ask for it leaves every run to
    improvise the same arguments and be refused the same way, before any work happens. That cost
    is small per run and permanent: on a fifteen-turn routine one rejected call is seven per cent
    of the run, every run, forever.

    Memory is not the fix, and this is the measurement that settles it. Of two routines found
    losing their first turn to the same refused call every run, one had the correct arguments
    written down in its own private notes the whole time and lost the turn anyway, while the
    other's recipe named no call at all. A note is read when a run thinks to look; a step is read
    because the run is in that step. So when a run establishes the working form of a call, it
    writes that form into the step, beside the instruction that needs it.

    Two limits keep this honest. Name the CAPABILITY and the shape of the request, never a
    tool's whole flag list — a copied list rots while the tool moves, which is the same reason
    the script index above points at docstrings. And treat a usage rejection as evidence the
    recipe is stale rather than as friction to absorb: confirm the current form from the tool
    itself, fix the step, and let the next run inherit a call that works."""


def bootstrap():
    """First run(s): create state/, understand the instruction's domain, and file deferred
    questions for genuinely pivotal unknowns (ask-policy). Advance state/phase.json to 'steady'
    once the basic loop can run, then continue into this run's normal work — a first fire that
    delivers nothing but setup costs the user a whole cadence."""


def pick_work():
    """From the instruction, the current phase, and any user messages, decide what this run
    delivers. Prefer finishing in-progress work; guard standing obligations first. Draw new items
    from SOURCES since SINCE_MARKER.

    Take everything that is genuinely due — this is a work LIST, not a token gesture. What a
    finished run delivers is the recipe's `## Done when`, accounted line by line at the finish.
    The turn budget is a runaway BACKSTOP, not a ration: do not stop early because turns are
    being spent; do not stretch a finished job to fill them."""


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


def update_state():
    """Bring the state files a later run reads up to date — what was delivered and
    SINCE_MARKER — and advance the phase to 'wrap-up' once the routine's FINISH LINE is reached
    (the operator's own terms for when this ROUTINE is finished, shown in the digest), so the next
    fire closes the job out instead of looking for more.

    Before writing a file under state/, apply the read-back test: will a LATER run read this? If
    yes it gets a STABLE name and is overwritten in place; if no it is scratch, so write it outside
    the durable state directory or delete it before finishing. A date or a record id embedded in a
    state filename is the signature of a file nobody will read again, because the next run
    computes a new name and looks for one that does not exist — so the directory grows without
    bound while every run reads only the handful of files the recipe actually names. A state
    update the run repeats the same way every time is one of the routine's own scripts, written
    once and called thereafter. Then sweep the run once for machinery friction you merely worked
    around — an action or tool that failed or misled you, a consent flow that asked for too much
    or too little — and file each real hitch with the `report` action before finishing (leave
    `target` unset if you cannot name the owner; triage routes it)."""


def wrap_up():
    """Terminal phase — the routine's finish line is reached and this is the closing run.
    Do three things and nothing else: VERIFY the DELIVERABLE one final time against the primary
    source (never against your own state files), TELL the user in plain words where it lives and
    how to reach it, and FINISH with the outcomes you prove accounted as met. Start no new work,
    draw nothing new from SOURCES, and open no new question."""
    return finish("ok", "Goal reached: deliverable verified, and where it lives.")


if __name__ == "__main__":
    main()
