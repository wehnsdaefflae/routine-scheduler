"""Train a model — advance a model by verified increments on a remote machine, run after run.

The mechanism: training and evaluation run as jobs on a MACHINE, detached from the run and taken
through the machine's own queue. Each run opens by polling every job it recorded, collects what
finished, scores each new checkpoint on a pinned held-out split and promotes a winner through the
REGISTRY. It then takes in the decisions the principal made on data candidates, chooses the next
increments from the LADDER (ingest material, build a dataset, curate candidates for review,
launch a training job, evaluate) and records the manifests the next run resumes from.

A run never holds itself open waiting for a long job. A job that finishes inside the run's
remaining budget may be awaited; anything longer is collected by a later run, or by the run its
completion notice starts where the routine has a trigger for it. While another routine holds the
machine, the run does the work that needs no GPU: data preparation, audits of disk and caches,
evaluation of what already exists.

What it needs from the outside world, as capabilities (the run finds the tools in its catalog and
records in its own memory which one worked):
- a bound machine: run commands, move files, submit a job to its queue and read the position
  back, read a job's log and exit file;
- read access to the data sources (a corpus of the principal's own writing, a public site's API);
- where the principal approves training data, the place his decisions land (a review folder he
  moves files out of);
- a model endpoint or a detector where the evaluation scores output with one.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the control flow below (its branches, loops and error
handling). The dummy imports name the parameters the clarifier pins down for the concrete
routine; decomposition turns the pattern into the routine's own recipe (main.md + stages/).
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — its type and meaning live in the trailing comment.
from routine.params import (
    MODEL,       # dict       — what is trained, the objective, the metric and the pinned held-out split it is judged on
    MACHINE,     # dict       — the machine jobs run on, how a job is submitted to its queue, where outputs and exit files land
    DATA,        # dict       — the sources with their cursors, how examples are built, and the approval step the principal performs on candidates, if any
    LADDER,      # list[str]  — the kinds of increment a run chooses from, in the order they usually unblock each other
    JOBS,        # str        — the jobs file: every submitted job with its id, purpose, expected finish and whether it was collected
    REGISTRY,    # str        — the checkpoint registry and the pointer through which the current best reaches its users
    TARGET,      # dict       — the finish line and its judge: a metric the run measures on the pinned split, or the principal's own judgement (then the run reports the distance and never declares it met)
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, edit_file, util, script, llm, view_image,
                             memory_read, memory_write, ask_user, report, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Train a model",
    "slug": "train-a-model",
    "description": "Advance a model by verified increments on a remote machine: collect finished "
                   "jobs first, score checkpoints on a pinned held-out split, promote the best "
                   "through the registry, take in the principal's data decisions, launch the "
                   "next increments through the machine's queue and record where everything "
                   "stands.",
    "when_to_use": "A long-running effort to train or tune a model on a GPU machine toward a "
                   "target a metric or the principal judges: jobs take hours, data grows between "
                   "runs, the principal may approve training data by hand. Each run resumes from "
                   "the manifests, never blocks on a running job and states the distance to the "
                   "target. Not for a fixed batch of files to process (drain-a-work-queue).",
    "version": 1,
    "tags": ["training", "model", "gpu", "remote-machine", "evaluation", "experiments"],
    "includes": ["make-failure-visible", "work-order", "verify-independently"],
    "tools": None,          # None = every action kind the routine's capabilities allow
}

# The lifecycle, recorded as {"phase": ...} in state/phase.json. The run gate reads that key.
#   bootstrap     — the first run: the pipeline on the machine, a first dataset, a baseline score;
#                   then improving.
#   improving     — increments are due every fire.
#   awaiting-data — the next experiment needs material only the principal or a source can supply.
#                   Skip-eligible: the gate may skip a fire when JOBS holds no job to collect, no
#                   approval changed and no source cursor has unread material.
#   maintain      — TARGET is met and holding; runs refresh the data and re-check the score.
#                   Skip-eligible on the same terms.
PHASES = ["bootstrap", "improving", "awaiting-data", "maintain"]

# What a finished run leaves behind, one line per outcome, each naming the step that produces it.
# The finish accounts for every line once: met (with the observation that shows it), unmet (with
# what remains for the next run) or not due (with how that was established).
DONE_WHEN = [
    "d1 · poll_jobs — every job in JOBS has its state read from the machine this run: running "
    "with its expected finish, finished and collected, or failed with its log read",
    "d2 · evaluate — every collected checkpoint is scored on the pinned held-out split, with the "
    "numbers taken from the machine's output",
    "d3 · promote — the registry and the published pointer name the best checkpoint by those "
    "scores",
    "d4 · reconcile_approvals — every decision the principal made on candidates since the last "
    "run is in the data manifest",
    "d5 · run_increment — every increment chosen this run is done and verified, or submitted with "
    "its job in JOBS",
    "d6 · record — the manifests, JOBS, the decisions log and state/phase.json let the next run "
    "resume; the distance to TARGET is stated from this run's numbers",
    "d7 · write_gate_state — state/gate.json holds the open jobs, the approval counts and the "
    "source cursors the gate compares",
]


class MachineHeld(Exception):
    """Another routine holds the machine's GPU: the queue position is recorded and the run moves to
    the increments that need no GPU."""


class NeedsDecision(Exception):
    """A choice only the principal can make (a data source to admit, a direction for the next
    experiment) — filed as a deferred question with options and one recommendation."""


class ExternalBlocker(Exception):
    """The machine or a source cannot be reached now. Prior state stays intact; the gap is named."""


def main():
    """One run: collect what the machine finished, score and promote, take in the principal's
    decisions, choose and run the next increments, record where everything stands."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through: the first run launches real work, so the second collects a result.

    finished = poll_jobs()                      # FIRST: every recorded job, before anything new
    for job in finished:
        evaluate(job)
    promote()
    reconcile_approvals()

    increments = choose_increments()
    if not increments:
        record()
        write_gate_state()
        return finish("ok", "Nothing due: every job collected, no approval changed, no new "
                            "material; the distance to TARGET stated.")

    for increment in increments:
        try:
            run_increment(increment)
        except MachineHeld:
            continue                            # the next increment that needs no GPU goes ahead
        except NeedsDecision as decision:
            ask_user(decision, mode="deferred")
        except ExternalBlocker:
            continue
        if not room_to_finish_cleanly():
            break

    record()
    write_gate_state()
    return finish("ok", "jobs collected, scores, promotion, increments run or submitted, the "
                        "distance to TARGET")


def orient():
    """Read the state digest, JOBS, the manifests and the decisions log before touching the machine.

    The digest carries the phase, the last result, the LEDGER tail and any messages or answers.
    Memory holds how the machine is reached, where the pipeline lives on it and what each tool
    does. The decisions log holds what the principal decided and which axes are closed."""


def bootstrap():
    """First run: stand the pipeline up on the machine, build a first dataset, score a baseline.

    Look each capability up in the CAPABILITIES catalog and write to memory how the machine, the
    sources and the registry are reached. Pin the held-out split now and record its definition:
    every later score is comparable only against the same split. Then set the phase to improving
    and launch a first real job."""


def poll_jobs():
    """Read every job in JOBS from the machine before anything new starts; return the finished ones.

    A running job keeps its expected finish; a finished one is collected (outputs pulled, the exit
    file read); a failed one has its log read and its error recorded. A job that finishes within
    the run's remaining budget may be awaited once; a longer one is left to a later run rather
    than waited on in a loop."""


def evaluate(job):
    """Score ONE collected checkpoint on the pinned held-out split, with numbers from the output.

    Every step of the evaluation chain fails loudly: a step that errors fails the evaluation; a
    missing output is a failure, never a zero or a skipped metric. The score is compared with the
    registry's best on the same split only."""


def promote():
    """Point the registry and the published pointer at the best checkpoint by the recorded scores.

    Promote only on a score measured this run or recorded from an earlier measurement of the same
    checkpoint on the same split. When TARGET's judge is the principal, promotion follows his
    stated preference; the run never declares his judgement for him. Not due when nothing
    collected beat the current best."""


def reconcile_approvals():
    """Take in the principal's decisions on data candidates since the last run.

    Where he decides by moving files (out of a review folder into an approved one), read both
    listings and update the data manifest: approved, declined, still waiting. A candidate he has
    not decided on stays in review; the run does not decide for him."""


def choose_increments():
    """Choose the increments this run carries out from LADDER, ordered by what unblocks what.

    Finish what a collected job made possible first (evaluate, promote), then the data the next
    experiment needs, then the experiment. The phase follows: awaiting-data when every remaining
    lever needs material only the principal or a source can supply, maintain when TARGET is met
    and holding. An empty list is an honest answer when nothing is due."""


def run_increment(increment):
    """Carry out ONE increment: ingest, build data, curate for review, or submit a training job.

    Data steps run as scripts on the machine, named for what they do. A training or evaluation
    job is submitted through the machine's queue with a deadline, its id and position recorded
    in JOBS, and a completion notice to the routine's trigger where one is configured; it is
    never waited on past the run's remaining budget. Raise MachineHeld when the queue shows the
    GPU taken by another routine, so the run turns to work that needs none. Candidates for the
    principal's review go to the review place with what he needs to judge them."""


def room_to_finish_cleanly():
    """True while the budget can run and verify the next increment and still record.

    A boundary check, never a ration: when it turns false, the next increment is named in the
    decisions log so the next run starts there."""


def record():
    """Update JOBS, the manifests and the decisions log; append one LEDGER entry; set the phase.

    The LEDGER entry names the jobs collected, the scores with their split, what was promoted,
    the increments run or submitted, and the distance to TARGET from this run's numbers. Record
    the phase under the key `phase`: the lifecycle label from PHASES, never the stage the run is
    in. Write every state file with the file actions or the routine's own scripts; a write made
    through a shell command is invisible to the checks that read the run's actions. Rotate
    LEDGER.md in the run its size passes the byte cap the recipe names — derived from this
    routine's own entries, set above the size of the tail it keeps with room for several more —
    and when a rotation leaves the file over the cap or within one entry of it, fix the numbers
    with that measurement. A file under state/ is one a later run reads, under a stable name
    overwritten in place; anything only this run needed is deleted before finishing. Machinery
    that failed or misled you and that you worked around goes to its owner with `report`."""
    ledger.append("jobs collected, scores + split, promoted, increments, distance to TARGET")


def write_gate_state():
    """Write state/gate.json, the run gate's view of the training effort, from this run's reading.

    It holds every job not yet collected with its expected finish, the approved and waiting
    candidate counts, each source cursor and the phase. The gate only reads this file and the run
    is its only writer; in improving the gate admits every fire whatever the file says."""
