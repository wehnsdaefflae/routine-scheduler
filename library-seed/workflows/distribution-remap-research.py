"""Iterative measured optimization — converge across many runs on a parameter vector.

Each run measures as many derivative-free candidates as it can actually VERIFY, moving a tunable
"vector" so that a MEASURED objective improves, and stops only when a real, verified measurement
clears the convergence test. The characteristic risks this pattern guards against are (a)
fabricating a result instead of measuring it, and (b) losing an expensive-to-find access path
between runs.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*,
one engine action per turn, following the control flow below. The dummy imports name the
parameters the clarifier pins down for the concrete task; the instruction (what is being
optimized, over what vector, against what objective) stays separate from this shape.
"""

# --- Parameter contract -------------------------------------------------------------------------
# None of these resolve at run time. Each names one thing the clarifier fixes for THIS routine.
from routine.params import (
    VECTOR_SPEC,        # str       — what parameters form the vector being stepped, and how they parameterize the transform
    OBJECTIVE,          # str       — how a candidate is scored, incl. the product/combination of the sub-metrics
    METRICS,            # list[str] — the sub-metrics that make up the objective (a target metric plus fidelity/plausibility guards)
    ACCESS_SPEC,        # str       — the compute/model resource each run needs, the permission it requires, how it is reached, and any REAL serialization it imposes (one job at a time, a rate limit, a quota per window)
    RESEARCH_TOPICS,    # list[str] — the literature areas to refresh each run
    STATE_ARTIFACTS,    # dict      — paths for persistent state: best-vector doc, experiment log, results table, related-work, next-step note
    CONVERGENCE_TEST,   # str       — the verified condition under which the loop has reached its goal
)

from routine.actions import (read_file, write_file, util, write_util, llm, spawn, subruns,
                             wait, ask_user, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Distribution Remap Research",
    "slug": "distribution-remap-research",
    "description": "Step a tunable vector toward a measured objective — as many candidates per run "
                   "as can be verified — with every number checked against real model and tool output.",
    "when_to_use": "Recurring instructions that optimize something empirically: a parameter vector "
                   "(sampler knobs, a distribution transform, a policy) is stepped against a "
                   "measured objective until a verified condition passes. Use it when runs "
                   "need remote model/compute access, a persistent best-so-far, and a rule that no "
                   "result may be claimed without measurement.",
    "version": 2,
    "tags": ["optimization", "research", "model-access", "measurement", "convergence"],
    "includes": ["evidence-discipline", "independent-verification", "web-research",
                 "failure-visibility", "decision-record"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "optimize", "converged"]     # tracked in state/phase.json


class AccessBlocked(Exception):
    """The remote resource can't be reached this run (permission, host, or script missing).
    Carries the precise diagnosis — the run's deliverable when it fires is that diagnosis, never
    a fabricated result."""


def main():
    """One optimization run — orient, secure access, refresh, measure what you can verify, record."""
    orient()                                        # know the current best vector and its scores first

    if phase.current() == "bootstrap":
        bootstrap()                                 # first run(s): lay down state/ then carry on

    if phase.current() == "converged":
        # A prior run already cleared the verified test. Re-confirm it against fresh output rather
        # than trusting the stored number — ONCE — then STAND THE ROUTINE DOWN.
        if reconfirm_convergence():
            stand_down()
            return finish("ok", "Convergence re-confirmed on fresh measurement; work is finished.")
        phase.set("optimize")                       # regression — reopen the search

    try:
        access = secure_access()                    # establish/confirm the remote path; persist on first success
    except AccessBlocked as blocked:
        # Do NOT invent numbers. The deliverable is exactly what is missing and how to fix it.
        record_blocked(blocked)
        return finish("blocked", str(blocked))

    refresh_research()                              # append genuinely new, dated, source-verified findings

    best = load_best()
    candidates = propose_candidates(best)           # everything this run can actually measure
    if not candidates:
        # The frontier is exhausted and the test still does not pass. ESTABLISH that, say it
        # plainly, and finish — never manufacture a move just to have taken one.
        update_next_step(best)
        record()
        return finish("ok", "No candidate worth measuring this run; frontier and next step noted.")

    for candidate in candidates:
        result = evaluate(candidate, access)        # generate under the candidate, MEASURE every metric
        accepted = improved(result, best)
        if accepted:
            promote(result)                         # update the best-vector doc with new scores
            best = result                           # later candidates this run step from the NEW best
        log_iteration(candidate, result, accepted=accepted)

        if converged(result):
            phase.set("converged")
            update_next_step(result)
            record()
            return finish("ok", "Verified convergence: candidate clears the test with fidelity intact.")
        if not room_to_measure_cleanly():
            break                                   # stop at a boundary — never a half-measured candidate

    update_next_step(best)                          # sharpen the direction the next run steps
    record()
    return finish("ok", "Candidates measured; best vector and results table updated, next step noted.")


def orient():
    """Consume the state digest, then read the persistent state under STATE_ARTIFACTS: the current
    best vector and its measured scores, the experiment log / results table, the related-work note,
    and the open-questions / next-step note. Check .memory/ for the recorded remote access path so
    discovery is not repeated. End this step knowing the current best vector AND its scores before
    acting on anything."""


def bootstrap():
    """First run(s): create state/ and the STATE_ARTIFACTS scaffolding (best-vector doc seeded with a
    plausible starting vector and clean-baseline scores, empty experiment log + results table,
    related-work note, next-step note). Advance state/phase.json to 'optimize' once the loop can run,
    then continue into this run's normal work — a first fire that only sets up costs a whole cadence."""


def secure_access():
    """Establish or confirm the path to the remote resource named by ACCESS_SPEC — the capability
    that runs inference/scoring against the actual weights and returns the full/large next-token
    distribution and samples under a supplied reshaping.

    If the required permission is not granted, the host is unreachable, or the inference script is
    absent, raise AccessBlocked with a precise diagnosis of what is missing (which permission, which
    host, which script). On the FIRST working path, persist to memory the exact invocation, the
    script location, the model id, and how per-token logprobs are obtained, so later runs skip
    discovery. Return a handle the later steps use to generate and score."""


def refresh_research():
    """Search recent work across RESEARCH_TOPICS (web-research). For each genuinely new item, READ
    the source to verify the claim — never summarize from memory — and append a dated line (title,
    link, one-line relevance) to the related-work note. Skip what is already recorded."""


def load_best():
    """Return the current best vector (the parameters described by VECTOR_SPEC) and its recorded
    objective, read from the best-vector doc — the starting point for this run's step."""


def propose_candidates(best):
    """Propose the next candidate vectors by small derivative-free moves from `best` — coordinate
    steps, hill-climb probes, a local pattern search — informed by the results table and the
    refreshed research. Order them most promising first.

    Take as many as this run can actually MEASURE and VERIFY end to end. The bound is the
    MEASUREMENT, never a per-run quota: a measurement is what makes a candidate real, so an
    unmeasured proposal is not progress, while a promising candidate left unmeasured is a whole
    cadence lost for nothing. Keep the discipline (every number from real output) and drop the
    arity.

    If the resource named by ACCESS_SPEC genuinely SERIALIZES the work — one job at a time on a
    shared machine, a rate-limited endpoint, a quota per window — that is a real external
    constraint: take what it allows and NAME it in the experiment log next to the number you took,
    so a later run can tell a limit from a habit. Absent such a constraint, take the whole
    promising frontier."""


def room_to_measure_cleanly():
    """True while the remaining budget can still generate, MEASURE and log the next candidate in
    full. A BOUNDARY check, never a ration: a half-measured candidate is not a partial result, it
    is a fabrication risk. When it goes false, stop and name the unmeasured candidates in the
    next-step note so the following run starts on them."""


def evaluate(candidate, access):
    """Generate text/output from the remote model under the candidate vector via `access`, alongside
    a clean baseline and a reference sample. MEASURE each metric in METRICS against real output:
    run the target scoring utility and record its verdict and numeric score; measure the fidelity
    guards (plausibility under the model, the variance/second-moment signature vs the reference,
    coherence/closeness to the clean baseline). Combine them into OBJECTIVE. Every number returned
    here comes from real tool/model output — a claimed-but-unmeasured value is the worst outcome
    this system knows (evidence-discipline, independent-verification). Return the candidate's
    measured metrics and combined objective."""


def improved(result, best):
    """True when the candidate's measured objective beats the current best's — and its fidelity
    guards stay within bounds, so a higher target score bought by degrading into gibberish does not
    count as an improvement."""


def promote(result):
    """Write the new best vector and its measured scores into the best-vector doc under
    STATE_ARTIFACTS, replacing the prior best only because THIS run measured it higher."""


def log_iteration(candidate, result, accepted):
    """Append the full iteration to the experiment log: candidate vector → output excerpt → target
    score → fidelity metrics → combined objective → accept/reject. Update the running results table
    so convergence (or its absence) is visible across runs."""


def update_next_step(result):
    """Rewrite the open-questions / next-step note with the direction to step next — which
    coordinate looked promising, which move to try, what this run's measurements ruled out, and any
    candidate proposed but left unmeasured — so the next run starts sharply instead of re-deriving
    the frontier."""


def converged(result):
    """True only when a real, this-run measurement satisfies CONVERGENCE_TEST — the target metric
    passes AND the fidelity guards hold on generated output, not on a stored or projected number."""


def reconfirm_convergence():
    """Re-run the convergence measurement on fresh output for the stored best vector. Return whether
    it still passes; a drift back below the bar reopens the search rather than resting on history."""


def stand_down():
    """The optimization is finished and re-confirmed once. Write the final best vector and its
    measured scores where the user will find them, tell them in plain words what was optimized and
    to what, and account the GOAL-scoped stopping conditions in `state/stopping.json` as met in the
    finish summary — meeting them is what retires the routine (a goal condition is sticky, and the
    scheduler stops firing on it). If the user has declared no goal condition, say plainly that the
    work is finished and that the routine can be retired.

    Do not fall back into the optimize loop. Re-measuring a converged vector on a schedule forever
    spends budget to re-learn a number nobody disputes."""


def record_blocked(blocked):
    """Persist the access diagnosis: append the blocked run to the experiment log (what was missing,
    what would unblock it) and note it in the next-step note, so the next run knows to fix access
    before optimizing. File the missing-permission / unreachable-host hitch with `report`
    (failure-visibility) so the owner sees it."""


def record():
    """Update state/phase.json and append exactly one LEDGER entry for the run (what moved, the
    measured scores, accept/reject, and why). Then sweep for machinery friction you merely worked
    around — an action that failed or misled, a consent flow that fit poorly — and file each real
    hitch with `report` before finishing (leave `target` unset if you cannot name the owner). When
    the experiment log or LEDGER grows past a comfortable size, rotate the older entries into an
    archive with a one-line rollup that run, keeping the recent tail."""
    ledger.append("vectors moved, measured scores, accept/reject, why, rejected candidates")


if __name__ == "__main__":
    main()
