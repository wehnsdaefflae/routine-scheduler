"""The observation renderer's two FLOORS: an error result every kind can word, and a renderer
that raises never ends the run.

WHY. Every kind's renderer reads the fields of its kind's SUCCESS — a line count, an exit code, a
child number. An observation that carries none of them (a refusal, an error, a call that has not
produced anything yet) reached those reads four times, and each time the `KeyError` ended the
engine mid-turn AND every resume after it, because a resumed leg re-renders the stored
observation: a background start (D118), the reserved finish turn's refusal (fifteen kinds at
once), a script's declaration gate (voice-model-trainer:20260910-030348), and memory_write's
edit-mode refusals (0.410.0), whose first live refusal cost scheduler-builder:20261011-120001
its summary after four shipped releases and then refused the resume at turn 0. Each was fixed
where it struck; the class stayed open, because nothing stood between any renderer and the loop.

1. AN ERROR RESULT (`failed`). An observation carrying `error` is worded here, ABOVE the per-kind
   chain, unless its kind is in `OWN_ERRORS` — the kinds whose own renderer words a failure. A
   kind that never had an error path (or gains one tomorrow) is therefore safe by default, and
   no failure is ever rendered as a success, nor cut at the generic line's 500 characters.

2. A RENDERER THAT RAISES (`defect_text`, `record`). `observations.render_observation` catches
   it and the run reads the RAW result instead — which still says what happened, so the run can
   act on it — beside one line naming the defect. A live turn records it as a transcript
   `error` (`where: render`) and the `observation_render_failed` health event, so it reaches the
   operator and the audit; a replay renders the same words and records nothing a second time.
   The text is a pure function of the observation, so a resume rebuilds the prompt byte for
   byte. An INCIDENT, not a standing condition: deliberately absent from the repeat cooldown.

The suite turns floor 2 into a failure (tests/render_guard.py): in a test a defect must FAIL,
never fall back — and every observation the dispatch seams return is rendered there, so a
handler test that produces a shape proves it renders. Floor 1 is held for every action kind by
tests/test_obs_floor.py, in both directions: a kind outside `OWN_ERRORS` gets the generic line,
and a kind inside it words the failure itself.
"""

from __future__ import annotations

import json
import logging
import traceback

from ..health_events import log_health_event

log = logging.getLogger(__name__)

#: The health event floor 2 files on a live turn. Deliberately not in `REPEAT_COOLDOWN_HOURS`.
EVENT = "observation_render_failed"

#: The kinds whose OWN renderer words an `error` result (each reads it before any success
#: field). Every other kind's failure is worded by `failed`.
OWN_ERRORS = frozenset({"read_file", "write_file", "edit_file", "memory_write", "util", "script",
                        "llm", "decide", "create_routine", "ask_user", "kill", "wait"})

#: How much of a raw result floor 2 shows — the observation cap's order of magnitude, so one
#: defect cannot flood the context.
RAW_CAP_CHARS = 6_000


def failed(obs: dict, kind: str) -> str | None:
    """Floor 1: the wording of an `error` result for a kind that has none of its own."""
    if kind in OWN_ERRORS or not (err := obs.get("error")):
        return None
    return f"OBSERVATION ({kind}{_subject(obs)} FAILED): {err}"


def _subject(obs: dict) -> str:
    """What the failed action was about, when the observation names it."""
    if obs.get("src"):
        return f" {obs['src']} → {obs.get('dst')}"
    return next((f" {obs[key]}" for key in ("path", "name", "handle") if obs.get(key)), "")


def describe(exc: BaseException) -> str:
    """The exception line plus the innermost frame — enough to find the renderer that broke."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return f"{type(exc).__name__}: {exc}"
    path = frames[-1].filename.rsplit("/rsched/", 1)[-1]   # the same on every checkout
    return f"{type(exc).__name__}: {exc} at {path}:{frames[-1].lineno}"


def defect_text(obs: dict, exc: Exception) -> str:
    """Floor 2: what the run reads when its observation's renderer raised."""
    kind = obs.get("kind")
    log.error("the observation renderer raised on a %r result", kind, exc_info=exc)
    raw = json.dumps({k: v for k, v in obs.items() if k != "media"},
                     ensure_ascii=False, default=str)
    if len(raw) > RAW_CAP_CHARS:
        raw = f"{raw[:RAW_CAP_CHARS]} […{len(raw) - RAW_CAP_CHARS} more chars]"
    return (f"OBSERVATION ({kind} — the engine could not word this result: {describe(exc)}. "
            "That is a defect in the engine, not in your input; your action's result follows "
            f"exactly as recorded, so read it as the outcome):\n{raw}")


def record(ctx, obs: dict, defect: str) -> None:
    """Floor 2 on a LIVE turn: name the defect where the operator and the audit look."""
    kind = obs.get("kind")
    ctx.transcript.event("error", {"where": "render", "kind": kind, "message": defect},
                         turn=ctx.turn)
    log_health_event(ctx.server.routines_home, EVENT, routine=ctx.routine.slug,
                     run_id=ctx.run_id,
                     detail=f"{kind} result: {defect} — the run read the raw result and went on")
