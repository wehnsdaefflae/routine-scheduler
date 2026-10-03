"""The escalation ladder's TRIGGER: when a rung fires, who it runs as, and what comes back.

`oversight.py` is the ladder's pure half (what travels up, what may come down). This module is
the impure half that the pure one deliberately excludes: it decides the MOMENT, starts the
supervisor as an ordinary child run, and files the directive. Step 4 of the ladder design
(docs/designs.md § The escalation ladder).

WHY A SEPARATE MODULE AND NOT `control.py`. The turn boundary already carries the pause gate,
five switch appliers, the inbox drain and the child announcements in 272 lines; the repo's
one-responsibility rule puts a mechanism with its own vocabulary, its own cost contract and its
own failure modes in its own file. `control._turn_boundary`'s caller gains one line.

WHY THE WORKER IS HELD WHILE THE RUNG RUNS. A directive about turn 40 that arrives at turn 46 is
advice about a run that no longer exists. The escalation therefore happens AT the boundary — the
worker's thread blocks on the supervisor's thread — and the worker's own turn counter does not
move, which is also what makes the cost arithmetic in the design honest: the rung's turns are
the rung's, pinned by `oversight_turns`, and never silently taken out of the worker's budget.

WHY A FAILED RUNG IS A NON-EVENT. Everything here is best-effort by construction: a supervisor
that crashes, times out, returns prose that is not a directive, or cannot be started at all
leaves the worker exactly as it was and writes one transcript event saying so. The ladder is an
oversight mechanism, not a dependency — a run must never fail because its supervision did.
A rung that cannot be STARTED (the tree's child budget is spent, the depth ceiling is reached)
is the ordinary case on a deep tree, not an error.

WHAT IS NOT HERE. `continue` injects nothing (constraint 1 of the design, and
`oversight.render_directive` already renders it to the empty string): this module asks that
question once, at the one place a message would be filed, so no future injection site can lose
the guarantee.

THE KNOBS AND WHO OWNS THEM (step 5). `ladder.enabled` and `ladder.max_depth` are the USER's and
live in `routine.yaml` (`config.base.DEFAULT_LADDER`); `ladder_rung_height` (`n`) and the rung's
own `oversight_turns` are machine-tunable and live in `tuning.yaml`, so a meta-routine can raise
`n` for a routine whose supervisor keeps answering `continue`. `enabled` defaults FALSE, so no
live routine changes behaviour at the release and the fleet is opted in one routine at a time.
"""

from __future__ import annotations

import logging

from ..config.base import DEFAULT_LADDER, DEFAULT_RUNG_HEIGHT
from . import child, inbox, oversight

log = logging.getLogger("rsched.engine.ladder")

#: The floor under a rung's own turn budget. Redundant for any `n >= 6` once the cap is derived
#: (below), and kept anyway: it costs nothing and catches someone setting `n = 2`. A rung with
#: one or two turns cannot read a dispatch AND answer — it spends its budget on the reading and
#: finishes with nothing, which reads as `continue` and is the one failure mode that is invisible.
MIN_OVERSIGHT_TURNS = 4


def oversight_turns_for(height: int) -> int:
    """A rung's own turn cap, derived from the interval it must read (operator, 2026-10-02).

    A rung's cost has a READ term — the `say` lines of the interval, which grows with `n` — and
    a JUDGE term — assemble one directive, fixed whatever `n` is. The design's `oversight_turns
    = n` prices BOTH as if both scaled, which is exactly the 2×-the-worker ceiling the design
    itself calls "the objection that decides the feature". A fixed cap prices both as fixed,
    which starves the read at large `n` and yields `oversight_no_directive`: supervision that
    silently does nothing.

    `n // 2 + 1` is sublinear read + constant. It still grows with the interval, so a long
    interval is never starved, while holding the per-rung worst case at a steady ~0.5× the
    worker's turns (n=10→6, 15→8, 20→11, 30→16) and the whole `m = 3` ladder at ~1.5× rather
    than ~6×. It is a FORMULA rather than a tuned constant because every routine that opts in
    inherits it; `tuning.yaml`'s `oversight_turns` overrides it per routine, so a meta-routine
    can raise it on measured evidence if real rungs are seen hitting the cap.
    """
    return max(height // 2 + 1, MIN_OVERSIGHT_TURNS)

#: The two defaults the CONFIG layer owns, read from it rather than restated here: a routine's
#: `ladder:` block already merges over `DEFAULT_LADDER`, and a second copy of `max_depth` in the
#: engine is how a knob comes to mean two things. `n` likewise lives in `config.base` beside the
#: tuning vocabulary that validates it.
DEFAULT_MAX_DEPTH = int(DEFAULT_LADDER["max_depth"])

#: The workflow pattern a supervisor rung runs on. NOT `general-task`: a run whose output BINDS
#: another run must be instructed in what a dispatch is, what the four verdicts mean and that it
#: answers with a directive and nothing else. `childrun.materialize_to_disk` degrades an unknown
#: slug to the builtin fallback recipe, so if this pattern is absent the rung is SKIPPED rather
#: than run on a recipe that never mentions supervision — a generic child given authority over a
#: run is worse than no supervision at all.
SUPERVISOR_WORKFLOW = "supervise-a-run"

#: The three events that pull an escalation forward (the design: `n` is a ceiling on the
#: interval, not a metronome). Each is a fact the engine already has at the boundary.
PULL_FORWARD = ("repeat_failure", "outcome_claimed_met", "worker_requested")


def supervisor_pattern_missing(server) -> str:
    """An empty string if the supervisor's workflow pattern resolves in the library, else why
    it does not.

    THE HARD SKIP (operator, 2026-10-02: option 1 + 3 together). `childrun.materialize_to_disk`
    catches every failure and degrades an unknown slug to the builtin fallback recipe — which
    for an ordinary child is the right trade and for a rung is the worst outcome in the design:
    a generic child, instructed to "orient, do the work, record", holding authority over a run.
    So the rung is SKIPPED when the pattern is absent and the skip is countable
    (`oversight_skipped`), rather than run on a recipe that never mentions supervision.

    This repo's standing contract is that the RECIPE is the truth of what a run is. Instructing
    the rung from `_supervisor_prompt` alone was rejected for that reason: it puts the recipe
    and the instruction in deliberate disagreement for the most authority-bearing run type in
    the system.
    """
    try:
        from ..workflows.library import workflows_dir

        home = getattr(server, "libraries_home", None)
        if home is None:
            return "no library home configured"
        if not (workflows_dir(home) / f"{SUPERVISOR_WORKFLOW}.py").is_file():
            return f"workflow pattern {SUPERVISOR_WORKFLOW!r} is not in the library"
    except Exception as exc:                      # an unreadable library is not a worker's problem
        return f"library unreadable ({exc})"
    return ""


def ladder_settings(ctx) -> dict:
    """The ladder's knobs for this run: `enabled`, `height` (n), `max_depth` (m) and the rung's
    own turn cap. Config (`routine.yaml`) is the user's and holds `enabled`/`max_depth`;
    `tuning.yaml` holds the machine-tunable `n`, so a meta-routine can raise it for a routine
    whose supervisor keeps answering `continue`.

    Reads both leniently and falls back to the defaults, because this runs at every turn
    boundary of every run: a malformed knob must leave the ladder off, never end a run.
    """
    cfg = getattr(ctx.routine, "ladder", None) or {}
    tuning = getattr(ctx.routine, "tuning", None) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not isinstance(tuning, dict):
        tuning = {}
    height = _positive(tuning.get("ladder_rung_height"), DEFAULT_RUNG_HEIGHT)
    turns = _positive(tuning.get("oversight_turns"), oversight_turns_for(height))
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "height": height,
        "max_depth": _positive(cfg.get("max_depth"), DEFAULT_MAX_DEPTH),
        "oversight_turns": max(turns, MIN_OVERSIGHT_TURNS),
    }


def _positive(value: object, fallback: int) -> int:
    """A whole number above zero, or the fallback — a knob a person typed is not a contract."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 \
        else fallback


def pull_forward_reason(loop) -> str | None:
    """Which of the three pull-forward events has fired since the last rung, or None.

    `repeat_failure` is the one the engine can see without asking anyone: the same action
    string failing identically twice is the shape of a loop, and `oversight.repetition_signals`
    already names it off the transcript. The other two are claims the worker makes — a
    finish-line outcome reported `met`, and an explicit request to escalate — and they are read
    off the run's own flags rather than inferred here.
    """
    repeat, claimed, requested = PULL_FORWARD
    if getattr(loop.ctx, "escalation_requested", False):
        return requested
    if getattr(loop.ctx, "outcome_claimed_met", False):
        return claimed
    signals = getattr(loop, "repeat_failure_signal", None)
    return repeat if signals else None


def rung_is_due(loop, settings: dict) -> str | None:
    """Why a rung fires now (`interval` or a pull-forward reason), or None.

    The interval is counted from the LAST rung, not from the run's start, so a pulled-forward
    escalation also resets the clock: a rung reports on the interval it owns, and two rungs must
    never judge the same turns twice (which is how a ladder becomes m copies of one audit).
    """
    if not settings["enabled"]:
        return None
    ctx = loop.ctx
    if ctx.depth + 1 > min(settings["max_depth"], ctx.budgets.max_subrun_depth):
        return None            # at the ladder's ceiling: the top rung answers to a person
    if (reason := pull_forward_reason(loop)) is not None:
        return reason
    since = ctx.turn - int(getattr(ctx, "last_rung_turn", 0))
    return "interval" if since >= int(getattr(ctx, "next_rung_in", 0) or settings["height"]) \
        else None


def _supervisor_prompt(dispatch: dict, *, rung: int) -> str:
    """The brief the supervisor run is started with: the dispatch as data, the three verdicts
    and dispositions it may answer with, and the one thing it must not do.

    The dispatch is handed over as JSON rather than as prose on purpose — the supervisor's value
    is the discrepancy between the worker's own words and the engine's counts, and prose about
    the counts would let a summary stand in for them.
    """
    import json
    return (
        f"You are rung {rung} of an escalation ladder, supervising the run whose dispatch "
        f"follows. You did not do this work and the run below cannot see you.\n\n"
        f"Judge the work on the DISPATCH alone: the run's goal, the `say` line of every action "
        f"it took in the interval, the action-kind histogram, the repetition signals, its "
        f"Done-when lines and finish line, its budget meter, and its own answers to "
        f"{len(oversight.WORKER_QUESTIONS)} questions. The worker's account and the engine's "
        f"counts are both in there; where they disagree, the counts are the evidence.\n\n"
        f"DISPATCH:\n```json\n{json.dumps(dispatch, ensure_ascii=False, indent=2)}\n```\n\n"
        f"Answer with ONE directive and nothing else — your finish summary IS the directive, as "
        f"a JSON object:\n"
        f'  verdict: one of {", ".join(oversight.VERDICTS)}\n'
        f'  disposition: one of {", ".join(oversight.DISPOSITIONS)}\n'
        f"  instruction: at most {oversight.MAX_INSTRUCTION_LINES} lines, and ONLY when the "
        f"disposition is not `continue`\n"
        f"  next_look: one line naming what you will look at next time\n"
        f"  next_rung_in: turns until the next escalation (at most the rung height)\n\n"
        f"`continue` is the right answer for a run that is working, and it reaches the worker as "
        f"NOTHING AT ALL — a healthy run must never learn it is being watched, because a "
        f"repeated signal in a prompt becomes the thing the run optimizes against. Do not "
        f"rewrite the run's recipe or its instruction: you speak into its conversation the way "
        f"its user does, and a directive that redefines the job is the one thing you may not "
        f"send."
    )


def _worker_answers(loop) -> list[str]:
    """The worker's half of the dispatch — the three questions only it can answer.

    Read from what the run has already SAID rather than bought with a turn: the recent `say`
    lines are the run's own account of what it is doing and why. A dedicated question turn
    would be the fuller way to ask, and it is also a turn the ladder spends on every rung of
    every run; the questions are named in the dispatch so a supervisor always knows which half
    is the worker SPEAKING and which half is the engine COUNTING.
    """
    says = [str(r.get("say") or "") for r in getattr(loop, "turn_records", [])[-3:]]
    return [s for s in says if s]


def _directive_from(sub, *, cap: int) -> dict | None:
    """The validated directive a finished rung handed back, or None if it handed back none.

    A rung's finish summary IS its directive (a directive cannot revise a recipe, so it needs
    no channel of its own). Prose that is not a directive, a malformed one, and a rung that
    failed are all the same thing here: no directive. `validate_directive` is the only judge —
    this module never repairs one, because a repaired directive is this module's opinion
    wearing a supervisor's authority.
    """
    import json
    raw = (getattr(sub, "summary", "") or "").strip()
    if not raw or getattr(sub, "status", "") not in ("ok", "partial"):
        return None
    if (start := raw.find("{")) >= 0 and (end := raw.rfind("}")) > start:
        raw = raw[start:end + 1]
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    try:
        return oversight.validate_directive(parsed, cap=cap)
    except Exception:
        return None


def escalate(loop, reason: str, settings: dict) -> None:
    """Run ONE rung at this turn boundary and act on what it says.

    Best-effort from end to end: every failure path leaves the worker exactly as it was and
    writes one transcript event. The worker's turn counter does not move, so the rung's cost is
    the rung's own.
    """
    ctx = loop.ctx
    rung = ctx.depth + 1
    if missing := supervisor_pattern_missing(ctx.server):
        # Checked BEFORE the dispatch is built and before the interval is closed: a rung that
        # cannot legitimately run must cost the worker nothing and must not make the next rung
        # re-judge turns this one never read.
        ctx.transcript.event("oversight_skipped", {"rung": rung, "reason": missing})
        return
    since = int(getattr(ctx, "last_rung_turn", 0))
    dispatch = oversight.build_dispatch(
        _transcript_path(ctx), goal=_goal(ctx), since_turn=since, turn=ctx.turn,
        stopping=_stopping(ctx), budget=_meter(ctx))
    # The worker's own half, beside the engine's counts and never merged into them: the
    # supervisor must always be able to tell which lines the run SAID from what the engine
    # COUNTED, because the discrepancy between the two is the finding.
    dispatch["worker_says"] = _worker_answers(loop)
    # The interval is closed before the rung runs, so a rung that fails cannot make the next
    # one re-judge the same turns — two rungs judging one interval is how a ladder becomes m
    # copies of one audit.
    ctx.last_rung_turn = ctx.turn
    ctx.escalation_requested = False
    ctx.outcome_claimed_met = False
    turns = settings["oversight_turns"]
    ctx.transcript.event("oversight_dispatch",
                         {"rung": rung, "reason": reason, "turn": ctx.turn,
                          "since_turn": since, "oversight_turns": turns})
    action = {"prompt": _supervisor_prompt(dispatch, rung=rung),
              "workflow": SUPERVISOR_WORKFLOW, "model": "main", "label": f"rung-{rung}"}
    sub = loop.subs._start_child(action, mode=child.OVERSIGHT, prefix="rung",
                                 overrides={"turns": turns})
    if isinstance(sub, str):
        # The ordinary case on a deep or budget-spent tree, not an error.
        ctx.transcript.event("oversight_skipped", {"rung": rung, "reason": sub})
        return
    sub.done.wait(timeout=_rung_deadline(settings))
    directive = _directive_from(sub, cap=settings["height"])
    if directive is None:
        ctx.transcript.event("oversight_no_directive",
                             {"rung": rung, "status": str(getattr(sub, "status", ""))})
        return
    ctx.next_rung_in = min(int(directive["next_rung_in"]), settings["height"])
    ctx.transcript.event("oversight_directive",
                         {"rung": rung, "verdict": directive["verdict"],
                          "disposition": directive["disposition"],
                          "next_rung_in": ctx.next_rung_in,
                          "next_look": directive.get("next_look", "")})
    text = oversight.render_directive(directive, rung=rung)
    if not text:
        return      # `continue`: the one disposition that injects NOTHING (constraint 1)
    inbox.file_message(ctx.routine.dir, text, via="oversight",
                       source=f"rung-{rung}")


def at_boundary(loop) -> None:
    """The ladder's whole footprint on the turn boundary: fire a rung if one is due.

    Wrapped so nothing in the ladder can end a run. An oversight mechanism that takes the run
    down with it when it breaks is worse than no oversight: the run was doing real work.
    """
    try:
        settings = ladder_settings(loop.ctx)
        if (reason := rung_is_due(loop, settings)) is not None:
            escalate(loop, reason, settings)
    except Exception as exc:
        log.warning("oversight rung skipped: %s", exc)
        try:
            loop.ctx.transcript.event("oversight_skipped", {"reason": f"error: {exc}"})
        except Exception:
            pass


def _rung_deadline(settings: dict) -> float:
    """How long the worker waits for its supervisor. Generous against the rung's turn cap and
    bounded all the same: a supervisor wedged on a model call must not hold a run forever, and
    losing one directive is far cheaper than losing the rest of the worker's budget.
    """
    return float(min(60 * settings["oversight_turns"], 900))


def _goal(ctx) -> str:
    """The run's goal VERBATIM — the instruction it was started with, never a paraphrase: a
    supervisor judging drift against a summary of the goal cannot see drift INTO that summary.
    """
    for attr in ("instruction", "task", "name"):
        if value := str(getattr(ctx.routine, attr, "") or ""):
            return value
    return ""


def _stopping(ctx) -> list[str]:
    """The recipe's Done-when lines plus the finish line — the dispatch's `stopping` half."""
    done = getattr(ctx.routine, "done_when", None) or []
    finish = getattr(ctx.routine, "finish_line", None) or []
    return [str(x) for x in (*done, *finish)]


def _meter(ctx) -> dict:
    """The budget meter, defensively: the dispatch wants it, and no reading of it is worth
    ending a run over.
    """
    try:
        return dict(ctx.meter())
    except Exception:
        return {}


def _transcript_path(ctx):
    """The run's own transcript file, which the dispatch is extracted from."""
    return ctx.transcript.path
