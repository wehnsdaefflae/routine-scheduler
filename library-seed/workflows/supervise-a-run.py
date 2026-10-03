"""Supervise a run — read one dispatch from the run below, answer with ONE directive.

A rung of the escalation ladder. Every other pattern in this library describes a run that DOES
something; this one describes a run whose entire output is a judgement about another run, and
whose authority is inverted: it is started by the engine on the worker's behalf and what it says
BINDS the run below it. That inversion is the reason it needs a recipe of its own — a generic
child, instructed to "orient, do the work, record", holding authority over a live run is worse
than no supervision at all.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*,
one engine action per turn. For a rung that is a short act: read the dispatch, judge it, finish.

WHAT A RUNG IS NOT. It does not do the worker's work, continue it, fix it, or go and look at the
worker's files, repository or outputs. It cannot see the run it judges and the run cannot see it;
the dispatch is the whole of its evidence, deliberately, because a supervisor that goes
exploring becomes a second worker with an opinion rather than an outside reading.

WHAT A RUNG MAY NOT DO, and why it is in the pattern rather than only in the prompt. It may not
revise the worker's recipe or rewrite its instruction — a directive is an APPEND to the worker's
conversation, spoken the way the user speaks into it. The USER outranks every rung. And the
worker may CONTEST a directive once, in its next dispatch, when the directive contradicts
something the supervisor could not see; a rung that meets a contest reads it as evidence, not as
insubordination, because otherwise its advantage in perspective becomes a disadvantage in
information.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve to anything at run time. Each names one piece of information the
# engine fixes for a rung before it starts.
from routine.params import (
    DISPATCH,        # dict — what travelled up: the goal verbatim, the interval's `say` lines,
                     #        the action-kind histogram, repetition signals, the Done-when lines
                     #        and finish line, the budget meter, and the worker's own three
                     #        answers under `worker_says` (kept separate, never merged in)
    RUNG,            # int  — which rung this is (1 reads the worker, 2 reads rung 1's verdicts,
                     #        3 reads the trend across rung 2's)
    RUNG_HEIGHT,     # int  — `n`, the ceiling on `next_rung_in`: a rung may shorten its own next
                     #        interval, never lengthen it past this
)

# A rung reads and answers. It takes no action on the world it judges — no file writes, no utils,
# no children, no outward acts — which is why `tools:` is narrow and the import line is short.
from routine.actions import read_file, finish

META = {
    "name": "Supervise a run",
    "slug": "supervise-a-run",
    "description": "One rung of the escalation ladder: read the dispatch from the run below, "
                   "judge it against the engine's own counts, answer with ONE directive.",
    "when_to_use": "Never chosen by a person or by a run — the engine starts a rung on this "
                   "pattern when a routine's ladder is enabled and an escalation is due "
                   "(docs/designs.md § The escalation ladder). Named here so the recipe a rung "
                   "runs on says what a rung is.",
    "version": 1,
    "tags": ["oversight", "ladder", "judgement", "meta"],
    "includes": ["evidence-discipline", "verify-independently"],
    "tools": ["read_file", "finish"],
}

PHASES = ["judge"]      # a rung is one short act; it has no cross-run progression

# What one finished rung leaves behind.
DONE_WHEN = [
    "d1 · read — the dispatch was read whole: the goal, the interval's own say lines, the "
    "engine's counts, and the worker's three answers as a separate account",
    "d2 · judge — the worker's account was checked AGAINST the counts, and every disagreement "
    "between the two is named with the count that contradicts it",
    "d3 · answer — the finish summary IS one valid directive and nothing else: verdict, "
    "disposition, instruction only when the disposition is not continue, next_look, next_rung_in",
]


class NotJudgeable(Exception):
    """The dispatch does not support a verdict (it is empty, or the interval holds no actions)."""


def main():
    """One rung — read, judge, answer. Three acts, usually three turns."""
    dispatch = read()

    try:
        verdict, disposition = judge(dispatch)
    except NotJudgeable:
        # Say so as a directive rather than inventing one. `on_track` + `continue` is the
        # honest answer to "I was given nothing to judge": it injects NOTHING into the worker,
        # so an empty interval costs the run not one token of context.
        return answer(verdict="on_track", disposition="continue",
                      next_look="whether the next interval carries actions at all")

    return answer(verdict=verdict, disposition=disposition,
                  instruction=instruction(verdict, disposition),
                  next_look=what_to_watch_next(dispatch),
                  next_rung_in=interval(verdict))


def read():
    """The dispatch is the evidence — read ALL of it before forming a view.

    Four parts, and they are not interchangeable. The GOAL is verbatim, never a paraphrase: a
    rung judging drift against a summary of the goal cannot see drift INTO that summary. The
    interval's `say` lines are what the run said it was doing, unsummarized at rung 1 — the party
    under suspicion must not be the only witness. The engine's COUNTS (the action-kind histogram,
    the repetition signals, the budget meter) are facts nobody authored. `worker_says` is the
    worker's own three answers: what it believes it has achieved, what it intends next and why,
    what it is stuck on.
    """


def judge(dispatch):
    """Judge the account against the counts — that gap is the finding no self-report produces.

    A worker's narration is coherent against whatever goal it is now pursuing, because each step
    followed plausibly from the last. So the question is never "does this sound reasonable": it
    is whether the account and the counts describe the same run.

    Where the two disagree, THE COUNTS ARE THE EVIDENCE. "Verifying the output" beside a
    histogram of fourteen reads and no writes is a drift finding. "Working through the queue"
    beside the same failed action string three times is a stall. A claim that an outcome is met,
    beside no action that could have produced it, is the one that matters most, because it is the
    claim that ends runs.

    The four verdicts, and each one's test:
      on_track   — the actions since the last rung move the stated goal, and the account matches
                   the counts.
      drifting   — the work is going somewhere, but not where the goal points: the goal has been
                   quietly redefined, or the run is polishing what already clears the bar.
      stalled    — the same ground is being covered again: an action repeated unchanged, a failure
                   retried identically, a read loop with no write.
      infeasible — the goal cannot be reached from here with what remains (the budget meter, a
                   dead external dependency, a precondition that will not arrive).

    Rung 2 asks a DIFFERENT question of a different object: not "is the work drifting" but "is
    the supervision any good" — a rung that rubber-stamps into a stall, or redirects on every
    dispatch, is its own failure mode and nothing below it can see that. Rung 3 reads the trend
    across rung 2's verdicts and asks whether the goal is still worth pursuing, the question no
    one below is positioned to ask, and escalates to a PERSON rather than to a fourth rung.
    """


def instruction(verdict, disposition):
    """At most five lines, and ONLY when the disposition is not `continue`.

    A directive is an append to a live conversation, so it is read as an instruction, not as
    advice. Write what the next interval should DO differently, in the worker's own terms — name
    the file, the item, the step. Do not restate the goal, do not explain the ladder, do not
    praise. A directive that a run cannot act on in its next turn is noise wearing authority.
    """


def what_to_watch_next(dispatch):
    """One line naming what this rung will look at next time — the commitment that makes the
    next judgement comparable to this one instead of a fresh impression.
    """


def interval(verdict):
    """`next_rung_in`: turns until the next escalation, capped at `n`.

    A rung may SHORTEN its own next interval and may never speak inside one. Shorten it when the
    verdict was `drifting` or `stalled` and a redirect was issued — the point of the shorter
    interval is to see whether the redirect took. Leave it at `n` when the run is on track: a
    repeated signal in the prompt does not merely inform a run, it becomes the thing the run
    optimizes against, and a healthy run must not learn that it is being watched on a clock.
    """


def answer(**directive):
    """The finish summary IS the directive — one JSON object and nothing else.

    No preamble, no prose around it, no second object. The engine validates it and a malformed
    directive is discarded whole: the rung then counts as having said nothing, which is the one
    failure mode of supervision that is invisible from below. So spend the last turn getting the
    shape right rather than the wording pretty.

      verdict       — on_track | drifting | stalled | infeasible
      disposition   — continue | redirect | narrow | abort_and_report
      instruction   — at most five lines, and ONLY when the disposition is not continue
      next_look     — one line: what this rung will examine next time
      next_rung_in  — turns until the next escalation, at most `n`

    `continue` is the disposition that injects NOTHING into the worker. Choose it whenever the
    run is doing its job: the ladder is meant to be invisible to a healthy run.
    """
    return finish("ok", "the directive, as one JSON object")
