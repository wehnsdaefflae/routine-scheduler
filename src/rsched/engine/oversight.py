"""The escalation ladder's pure half: the DISPATCH that travels up, the DIRECTIVE that comes down.

Nothing here calls a model, opens a socket or starts a run. That is the point: the judgement is
the expensive, model-shaped part, and it is deliberately NOT in this module, so the two things
that carry authority — what a supervisor is shown, and what it is allowed to say back — can be
tested against a recorded transcript and a dict.

VOCABULARY, fixed by the design so it cannot drift the way F338's three names did: the run doing
the work is the **worker** (rung 0), each supervisor is one **rung** up, a **dispatch** travels
up, a **directive** comes down, `n` is the rung height (turns between escalations) and `m` the
ladder height.

WHY A DISPATCH CARRIES RAW `say` LINES. The party under suspicion must not be the only witness.
A worker that has drifted writes a coherent account *against the drifted goal*, because each step
followed plausibly from the last — so the supervisor is given the worker's own words AND the
engine's own counts, and the discrepancy between "verifying the output" and a histogram showing
14 `read_file` / 0 `write_file` is a finding no self-authored summary can produce. The raw lines
are not summarized at rung 1 for that reason; compression starts at rung 2, which reads rung 1's
verdicts rather than the worker's turns.

WHY `continue` RENDERS TO THE EMPTY STRING. Budget warnings once rode every observation past 85%,
which "made it a countdown, and runs converged at the ceiling whether or not their stopping
conditions were met" (CLAUDE.md § Core contracts). A repeated signal in the prompt does not merely
inform a run; it becomes the thing the run optimizes against. So a healthy run must never learn
that it is being watched: `continue` writes a transcript event and injects nothing, and the
schema below refuses a `continue` that carries instruction prose at all, so the guarantee cannot
be lost at some future injection site.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

#: A dispatch's fixed shape. Named so a caller (and a test) can assert on the contract rather
#: than on whichever keys today's builder happens to emit.
DISPATCH_KEYS = (
    "goal", "since_turn", "turn", "says", "histogram", "repetition", "stopping", "budget",
    "worker_questions",
)

#: The three questions only the worker can answer. The engine assembles everything else; these
#: are what the worker fills in, and they are deliberately about intent rather than about
#: progress — a progress question invites the drifted summary this whole mechanism exists to
#: get a second opinion on.
WORKER_QUESTIONS = (
    "What do you believe you have achieved since the last rung?",
    "What do you intend to do next, and why that?",
    "What are you stuck on?",
)

#: What a supervisor may CONCLUDE.
VERDICTS = ("on_track", "drifting", "stalled", "infeasible")

#: What a supervisor may DO about it. `continue` is the only one that injects nothing.
DISPOSITIONS = ("continue", "redirect", "narrow", "abort_and_report")

#: At most five lines of instruction — a supervisor that writes an essay is rewriting the recipe,
#: which is the one thing a directive may never do.
MAX_INSTRUCTION_LINES = 5

#: How many identical action strings in one interval count as repetition rather than as rhythm.
#: Two is a retry; three is a loop.
REPETITION_AT = 3


def _action_signature(payload: dict) -> str:
    """The stable identity of an action, for repetition detection.

    `say` is deliberately EXCLUDED: a run looping on one broken call often re-words its narration
    while re-emitting the identical call, and a signature that included the prose would read that
    as progress. Everything else in the payload is included, key-sorted, so two actions match
    only when the engine would do the same thing twice.
    """
    rest = {k: v for k, v in payload.items() if k != "say"}
    return json.dumps(rest, sort_keys=True, ensure_ascii=False, default=str)


def _observation_failed(payload: Any) -> bool:
    """Did this observation report a failure? Read permissively, because an observation payload
    is not one shape: some carry `ok`, some an `exit_code`, some only prose. A missed failure
    costs a repetition signal; a false one would cry loop on a healthy run, so `ok is False` and
    a nonzero exit are the only positives, never the mere presence of the word error.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("ok") is False:
        return True
    code = payload.get("exit_code")
    return isinstance(code, int) and code != 0


def read_actions(transcript: Path, *, since_turn: int) -> tuple[list[dict], dict[int, bool]]:
    """Every `assistant_action` after `since_turn`, plus which turns' observations failed.

    Fail-quiet on a malformed line by design: a transcript is appended to by a live process, so
    the last line may be a partial write, and an oversight dispatch that raised on it would take
    down the run it was hired to help. A line that will not parse contributes nothing.
    """
    actions: list[dict] = []
    failed: dict[int, bool] = {}
    try:
        text = transcript.read_text(encoding="utf-8")
    except OSError:
        return actions, failed
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        turn = ev.get("turn")
        if not isinstance(turn, int) or turn <= since_turn:
            continue
        etype = ev.get("type")
        if etype == "assistant_action" and isinstance(ev.get("payload"), dict):
            actions.append(ev)
        elif etype == "observation" and _observation_failed(ev.get("payload")):
            failed[turn] = True
    return actions, failed


def repetition_signals(actions: list[dict], failed: dict[int, bool]) -> list[str]:
    """The two loop shapes the engine can see WITHOUT asking the worker.

    1. the same action string emitted three or more times in the interval;
    2. an action whose observation FAILED and which was then re-emitted unchanged.

    The second is not a subset of the first: two turns is not yet repetition by (1), but a call
    that failed and was retried byte-identically is already a loop, and it is the commonest one
    in this fleet's transcripts (a util rejected on its own usage line, re-sent verbatim).
    """
    signatures = [_action_signature(a["payload"]) for a in actions]
    counts = Counter(signatures)
    out: list[str] = []

    for sig, n in counts.most_common():
        if n < REPETITION_AT:
            continue
        pairs = list(zip(actions, signatures, strict=True))
        kind = next((a["payload"].get("kind", "?") for a, s in pairs if s == sig), "?")
        name = next((a["payload"].get("name") for a, s in pairs
                     if s == sig and a["payload"].get("name")), None)
        what = f"{kind}:{name}" if name else str(kind)
        out.append(f"the same {what} action was emitted {n} times in this interval")

    for i in range(1, len(actions)):
        prev, cur = actions[i - 1], actions[i]
        if signatures[i] != signatures[i - 1]:
            continue
        if not failed.get(prev.get("turn", -1)):
            continue
        kind = cur["payload"].get("kind", "?")
        name = cur["payload"].get("name")
        what = f"{kind}:{name}" if name else str(kind)
        msg = (f"a {what} action failed at turn {prev.get('turn')} and was retried "
               f"unchanged at turn {cur.get('turn')}")
        if msg not in out:
            out.append(msg)

    return out


def build_dispatch(transcript: Path, *, goal: str, since_turn: int, turn: int,
                   budget: dict | None = None, stopping: Any = None) -> dict:
    """What a rung is shown. Pure extraction from what is already on disk or in the meters.

    An EMPTY interval is still a dispatch — a worker that emitted nothing since the last rung is
    itself the signal, and a builder that returned None there would make the stalled run the one
    case the ladder cannot supervise.
    """
    actions, failed = read_actions(Path(transcript), since_turn=since_turn)
    payloads = [a["payload"] for a in actions]
    return {
        "goal": goal,
        "since_turn": since_turn,
        "turn": turn,
        "says": [str(p.get("say", "")) for p in payloads],
        "histogram": dict(Counter(str(p.get("kind", "?")) for p in payloads)),
        "repetition": repetition_signals(actions, failed),
        "stopping": stopping,
        "budget": dict(budget or {}),
        "worker_questions": list(WORKER_QUESTIONS),
    }


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def validate_directive(directive: Any, *, cap: int) -> dict:
    """Accept a supervisor's reply, or refuse it NAMING THE FIELD.

    Every rejection says which field and why, because a supervisor whose output is silently
    coerced is a supervisor whose authority nobody can audit — and this object BINDS a run.

    `cap` is the operator's `n`: `next_rung_in` is clamped to it rather than refused, because a
    supervisor shortening its own next interval is sanctioned and lengthening it past `n` is the
    one way a rung could quietly switch itself off.
    """
    _require(isinstance(directive, dict), "a directive must be a JSON object")
    d = dict(directive)

    verdict = d.get("verdict")
    _require(verdict in VERDICTS,
             f"verdict must be one of {list(VERDICTS)}, got {verdict!r}")
    disposition = d.get("disposition")
    _require(disposition in DISPOSITIONS,
             f"disposition must be one of {list(DISPOSITIONS)}, got {disposition!r}")

    instruction = d.get("instruction") or []
    _require(isinstance(instruction, list),
             "instruction must be a list of lines, one thing to do per line")
    _require(all(isinstance(x, str) for x in instruction),
             "every instruction line must be a string")
    _require(len(instruction) <= MAX_INSTRUCTION_LINES,
             f"instruction may carry at most {MAX_INSTRUCTION_LINES} lines, got "
             f"{len(instruction)} — a longer one is rewriting the recipe, which a directive "
             f"may never do")

    if disposition == "continue":
        # Constraint 1 of the design, enforced HERE rather than at the injection site, so the
        # guarantee survives a future second caller: a healthy run learns nothing.
        _require(not instruction,
                 "a `continue` disposition must carry no instruction: a healthy run's context "
                 "stays untouched, or the ladder becomes a countdown the run optimizes against")
    else:
        _require(bool(instruction),
                 f"a `{disposition}` disposition must say what to do instead — a redirect with "
                 f"nothing in it is a stall wearing a verdict")

    if "next_rung_in" in d and d["next_rung_in"] is not None:
        nri = d["next_rung_in"]
        _require(isinstance(nri, int) and not isinstance(nri, bool),
                 "next_rung_in must be a whole number of turns")
        _require(nri >= 1, "next_rung_in must be at least 1 turn")
        d["next_rung_in"] = min(nri, cap)

    if "next_look" in d and d["next_look"] is not None:
        _require(isinstance(d["next_look"], str), "next_look must be one line of prose")

    return d


def render_directive(directive: dict, *, rung: int) -> str:
    """The directive as the WORKER reads it — or the empty string when it says nothing.

    Returning "" for a `continue` is the contract, not an optimization: the caller injects only
    a non-empty rendering, so there is exactly one place where "a healthy run is never told it is
    supervised" can be true or false, and it is here.

    The voice is deliberately that of an instruction arriving in the conversation, not of a
    system notice: a directive speaks into the run the way the user does, and it may not revise
    the recipe or rewrite the instruction.
    """
    if directive.get("disposition") == "continue":
        return ""

    lines = [f"OVERSIGHT DIRECTIVE (rung {rung}) — verdict: {directive.get('verdict')} · "
             f"disposition: {directive.get('disposition')}"]
    lines.extend(f"- {line}" for line in directive.get("instruction") or [])
    if directive.get("next_look"):
        lines.append(f"Next rung will look at: {directive['next_look']}")
    lines.append("This came from a supervisor that cannot see your context and that you cannot "
                 "see. A user message outranks it. If it contradicts something it could not "
                 "have known, say so once in your next dispatch.")
    return "\n".join(lines)


__all__ = [
    "DISPATCH_KEYS",
    "DISPOSITIONS",
    "MAX_INSTRUCTION_LINES",
    "REPETITION_AT",
    "VERDICTS",
    "WORKER_QUESTIONS",
    "build_dispatch",
    "read_actions",
    "render_directive",
    "repetition_signals",
    "validate_directive",
]
