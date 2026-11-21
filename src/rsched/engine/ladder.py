"""The escalation ladder's TRIGGER: when a rung fires, who it runs as, and what comes back.

`oversight.py` is the ladder's pure half (what travels up, what may come down); this is the
impure half: it decides the MOMENT, starts the supervisor as an ordinary child run, and files
the directive.

The worker is HELD while the rung runs (a late directive is advice about a run that no longer
exists), and its turn counter does not move: the rung's turns are its own (`oversight_turns`).
Everything is best-effort: a rung that fails or cannot start leaves the worker as it was and
writes one transcript event — a run never fails because its supervision did. `continue`
injects nothing, decided once, at the one place a message is filed.

Knobs: `ladder.enabled` (default off) and `ladder.rung_height` (the recursion limit) are the
USER's, in `routine.yaml`; `ladder_turn_interval` (`n`) and `oversight_turns` are
machine-tunable, in `tuning.yaml`.
"""

from __future__ import annotations

import logging

from ..config.base import DEFAULT_LADDER, DEFAULT_TURN_INTERVAL
from . import child, inbox, laddergoal, oversight, switches

log = logging.getLogger("rsched.engine.ladder")

#: Re-exports, so a name has ONE spelling for its callers wherever it is defined.
MIN_OVERSIGHT_TURNS = oversight.MIN_OVERSIGHT_TURNS
oversight_turns_for = oversight.oversight_turns_for
REQUESTED_BY_PERSON = switches.REQUESTED_BY_PERSON

#: Read from the CONFIG layer that owns it, never restated here.
DEFAULT_RUNG_HEIGHT = int(DEFAULT_LADDER["rung_height"])

#: The workflow pattern a supervisor rung runs on: a run whose output BINDS another run must be
#: instructed in supervision. Absent, the rung is SKIPPED (`supervisor_pattern_missing`).
SUPERVISOR_WORKFLOW = "supervise-a-run"

#: The three events that pull an escalation forward (`n` is a ceiling, not a metronome).
PULL_FORWARD = ("repeat_failure", "outcome_claimed_met", "worker_requested")


def supervisor_pattern_missing(server) -> str:
    """An empty string if the supervisor's workflow pattern resolves in the library, else why
    it does not.

    THE HARD SKIP: `childrun.materialize_to_disk` would degrade an unknown slug to the generic
    fallback recipe — a generic child holding authority over a run. So the rung is SKIPPED,
    countably (`oversight_skipped`); the RECIPE stays the truth of what a run is.
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
    """The ladder's knobs for this run: `enabled`, `turn_interval` (n), `rung_height` (m, the
    recursion limit) and the rung's own turn cap. Read leniently with default fallbacks: a
    malformed knob must leave the ladder off, never end a run.
    """
    cfg = getattr(ctx.routine, "ladder", None) or {}
    tuning = getattr(ctx.routine, "tuning", None) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not isinstance(tuning, dict):
        tuning = {}
    interval = _positive(tuning.get("ladder_turn_interval"), DEFAULT_TURN_INTERVAL)
    turns = _positive(tuning.get("oversight_turns"), oversight_turns_for(interval))
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "turn_interval": interval,
        "rung_height": _positive(cfg.get("rung_height"), DEFAULT_RUNG_HEIGHT),
        "oversight_turns": max(turns, MIN_OVERSIGHT_TURNS),
    }


def _positive(value: object, fallback: int) -> int:
    """A whole number above zero, or the fallback — a knob a person typed is not a contract."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 \
        else fallback


def pull_forward_reason(loop) -> str | None:
    """Which of the three pull-forward events has fired since the last rung, or None.

    `repeat_failure` the engine sees itself; the other two are the worker's own claims, read
    off the run's flags.
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

    Counted from the LAST rung, not the run's start, so a pulled-forward escalation resets the
    clock too: two rungs must never judge the same turns. A pull-forward fires whatever the
    interval says — never over an empty window (`oversight.worth_a_rung`).
    """
    if not settings["enabled"]:
        return None
    ctx = loop.ctx
    if ctx.depth + 1 > min(settings["rung_height"], ctx.budgets.max_child_depth):
        return None            # at the ladder's ceiling: the top rung answers to a person
    since = ctx.turn - int(getattr(ctx, "last_rung_turn", 0))
    if (reason := pull_forward_reason(loop)) is not None:
        return reason if oversight.worth_a_rung(since) else None
    return "interval" if since >= int(getattr(ctx, "next_rung_in", 0)
                                      or settings["turn_interval"]) else None


def _supervisor_prompt(dispatch: dict, *, rung: int) -> str:
    """The brief the supervisor run is started with: the dispatch as data, the three verdicts
    and dispositions it may answer with, and the one thing it must not do.

    The dispatch is JSON on purpose: prose about the counts would let a summary stand in for
    them.
    """
    import json
    return (
        f"You are rung {rung} of an escalation ladder, supervising the run whose dispatch "
        f"follows. You did not do this work and the run below cannot see you.\n\n"
        f"Judge the work on the DISPATCH alone: the run's goal, the `say` line of every action "
        f"it took in the interval, the action-kind histogram, the repetition signals, its "
        f"Done-when lines and finish line, its budget meter, and its newest lines again under "
        f"`worker_says`. The {len(oversight.WORKER_QUESTIONS)} `worker_questions` are YOURS to "
        f"answer FROM that evidence — the worker was never asked them and answered nothing. "
        f"Its account and the engine's counts are both there; where they disagree, the counts "
        f"are the evidence.\n\n"
        f"DISPATCH:\n```json\n{json.dumps(dispatch, ensure_ascii=False, indent=2)}\n```\n\n"
        f"Answer with ONE directive and nothing else — your finish summary IS the directive, as "
        f"a JSON object:\n"
        f'  verdict: one of {", ".join(oversight.VERDICTS)}\n'
        f'  disposition: one of {", ".join(oversight.DISPOSITIONS)}\n'
        f"  instruction: at most {oversight.MAX_INSTRUCTION_LINES} lines, and ONLY when the "
        f"disposition is not `continue`\n"
        f"  next_look: one line naming what you will look at next time\n"
        f"  next_rung_in: turns until the next escalation (at most the turn interval)\n\n"
        f"`continue` is the right answer for a run that is working, and it reaches the worker as "
        f"NOTHING AT ALL — a healthy run must never learn it is being watched, because a "
        f"repeated signal in a prompt becomes the thing the run optimizes against. Do not "
        f"rewrite the run's recipe or its instruction: you speak into its conversation the way "
        f"its user does, and a directive that redefines the job is the one thing you may not "
        f"send."
    )


def _worker_answers(loop) -> list[str]:
    """The worker's THREE MOST RECENT `say` lines — its newest account, nothing more.

    Not answers — nobody asked the worker anything; `WORKER_QUESTIONS` is the RUNG's
    checklist. This is for RECENCY: the newest intent is the one a verdict turns on.
    """
    says = [str(r.get("say") or "") for r in getattr(loop, "turn_records", [])[-3:]]
    return [s for s in says if s]


def _directive_from(sub, *, cap: int) -> dict | None:
    """The validated directive a finished rung handed back, or None if it handed back none.

    A rung's finish summary IS its directive. Non-directive prose, a malformed one and a failed
    rung are all no directive. `validate_directive` is the only judge — never repaired here.
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
        # Checked BEFORE the interval is closed: a rung that cannot run costs nothing.
        ctx.transcript.event("oversight_skipped", {"rung": rung, "reason": missing})
        return
    since = int(getattr(ctx, "last_rung_turn", 0))
    dispatch = oversight.build_dispatch(
        _transcript_path(ctx), goal=laddergoal.goal(loop), since_turn=since, turn=ctx.turn,
        stopping=laddergoal.stopping(loop), budget=_meter(ctx))
    # The newest three say lines, beside the counts and never merged into them.
    dispatch["worker_says"] = _worker_answers(loop)
    # The interval is closed before the rung runs, so a failed rung's turns are not re-judged.
    ctx.last_rung_turn = ctx.turn
    ctx.escalation_requested = False
    ctx.outcome_claimed_met = False
    turns = settings["oversight_turns"]
    ctx.transcript.event("oversight_dispatch",
                         {"rung": rung, "reason": reason, "turn": ctx.turn,
                          "since_turn": since, "oversight_turns": turns})
    action = {"prompt": _supervisor_prompt(dispatch, rung=rung),   # `supervisor`: MAIN till set
              "workflow": SUPERVISOR_WORKFLOW, "model": "supervisor", "label": f"rung-{rung}"}
    sub = loop.subruns._start_child(action, mode=child.OVERSIGHT, prefix="rung",
                                    overrides={"turns": turns})
    if isinstance(sub, str):
        # The ordinary case on a deep or budget-spent tree, not an error.
        ctx.transcript.event("oversight_skipped", {"rung": rung, "reason": sub})
        return
    sub.done.wait(timeout=_rung_deadline(settings))
    directive = _directive_from(sub, cap=settings["turn_interval"])
    if directive is None:
        ctx.transcript.event("oversight_no_directive",
                             {"rung": rung, "status": str(getattr(sub, "status", ""))})
        return
    ctx.next_rung_in = min(int(directive["next_rung_in"]), settings["turn_interval"])
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

    Wrapped so nothing in the ladder can end a run.
    """
    try:
        settings = ladder_settings(loop.ctx)
        # A PERSON's ask comes first and is not gated on `enabled` (off by default).
        if (reason := switches.person_asked(loop)) is None:
            reason = rung_is_due(loop, settings)
        if reason is not None:
            escalate(loop, reason, settings)
    except Exception as exc:
        # The mechanism RAISED — not a rung declining to run, so its own record, distinct from
        # a skip: total failure must never read as a healthy decline.
        log.warning("oversight rung FAILED: %s", exc)
        try:
            loop.ctx.transcript.event("oversight_failed",
                                      {"error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass


def _rung_deadline(settings: dict) -> float:
    """How long the worker waits for its supervisor: generous, but bounded — losing one
    directive is cheaper than a wedged supervisor holding the run.
    """
    return float(min(60 * settings["oversight_turns"], 900))


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
