"""The SHAPE of a run's recent actions — what the identical-action streak cannot see.

`loopnudge.repeat_streak` catches ONE action landing again and again. Hermes Agent's tool-loop
controller (`agent/tool_guardrails.py`) showed three more shapes a stuck run takes, each with
every action individually valid, and this module reads them over the same landed actions:

- a CYCLE — two to four actions taking turns (`read → edit → read → edit`) with the SAME results
  each time round. The results are part of the key on purpose: a poll that is making progress
  changes what it returns, and a cycle of unchanged results is a run that is not moving. Kinds
  whose job is to wait (`wait`, `children`, `ask_user`) never form one.
- VARIED FAILURES — three DIFFERENT calls of one kind failing in a row. The identical streak
  misses it by construction (each call differs), and it is the signature of guessing at a fix
  instead of reading the error. `shell` is exempt, as Hermes exempts its terminal: a run of
  distinct red commands is usually diagnosis.
- a SAME RESULT — a large result (`SAME_MIN_CHARS`+) byte-identical to one the run was already
  shown and still holds in its context. The observation is MARKED (`same_as_turn`) before it is
  recorded, and `engine/samestub.py` renders the mark as one line, live and on replay alike; the
  record keeps the full result. Only when the earlier copy is verbatim in the live message list:
  after a compaction archived it, the result is shown again in full.

The two warnings ride the observation tail (`loop._tails`), each once per onset, and file a
`wrong_turn_detected` row (`action_cycle`, `varied_failures`) — the same measurement-first
discipline `engine/wrongturn.py` sets out: a warning, never an ended run.

The state lives on `loop.shape` and is NOT rebuilt on a resume: every reading here is about the
last few actions, and a fresh leg starting with an empty window misses at most one onset.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field

from . import hold, wrongturn

#: Cycle lengths read. 1 is the identical streak's (`loopnudge`); past 4 a "cycle" is a plan.
CYCLE_PERIODS = (2, 3, 4)

#: Different failing calls of one kind, in a row, before the run is told to stop varying.
VARIED_FAILS = 3

#: The smallest result worth standing in for — below it the stub is not shorter.
SAME_MIN_CHARS = 512

#: Kinds whose job is to wait for something to change: never part of a cycle.
_WAITING = frozenset({"wait", "children", "ask_user", "finish", "do_nothing"})

#: Kinds whose result may be replaced by the same-result line: reads and calls whose output
#: is the whole point, never a write's confirmation or a decision's record.
STUB_KINDS = frozenset({"read_file", "memory_read", "read_rule", "util", "script", "shell",
                        "websearch", "code_search"})

#: Side fields that ride an action without changing what it does.
_SIDE = frozenset({"say", "note", "remind", "remind_feedback", "role_swap"})


@dataclass
class Shape:
    """One run's reading of its recent actions (`loop.shape`)."""

    recent: deque = field(default_factory=lambda: deque(maxlen=2 * max(CYCLE_PERIODS)))
    cycle_warned: tuple = ()
    fail_kind: str = ""
    fail_calls: set = field(default_factory=set)
    fail_warned: bool = False
    results: dict = field(default_factory=dict)
    warnings: int = 0   # warnings given this run — read by the review leg's triggers


def configure(loop) -> None:
    """At init (`loopsetup.configure`)."""
    loop.shape = Shape()


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def after(loop, action: dict, obs: dict) -> str:
    """Read one landed action and what came back; return the tail it earns ("" mostly).

    Called from `loop._tails`, BEFORE the observation is recorded and rendered — which is what
    lets a same-result mark ride both. A HELD action never ran, so it reads as nothing here.
    """
    shape: Shape | None = getattr(loop, "shape", None)
    if shape is None or hold.is_hold(obs):
        return ""
    from .observations import format_observation, is_failure

    kind = str(action.get("kind") or "")
    call = _digest({k: v for k, v in action.items() if k not in _SIDE})
    full = format_observation(obs)
    _mark_same(loop, shape, kind, obs, full)
    tail = _cycle(loop, shape, kind, call, _digest(full))
    if is_failure(obs) and kind != "shell":
        tail += _failed(loop, shape, kind, call)
    else:
        shape.fail_kind, shape.fail_calls, shape.fail_warned = "", set(), False
    return tail


def _mark_same(loop, shape: Shape, kind: str, obs: dict, full: str) -> None:
    """Mark `obs` as a repeat of an earlier result the run still holds, or remember this one."""
    if (kind not in STUB_KINDS or len(full) < SAME_MIN_CHARS or obs.get("not_executed")
            or obs.get("media")):
        return
    key = _digest(full)
    turn = int(loop.ctx.turn)
    earlier = shape.results.get(key)
    if earlier is not None and earlier != turn and any(
            full in str(m.get("content") or "") for m in reversed(loop.messages)):
        obs["same_as_turn"] = earlier
        obs["same_chars"] = len(full)
        return
    shape.results[key] = turn


def _cycle(loop, shape: Shape, kind: str, call: str, result: str) -> str:
    shape.recent.append((kind, call, result))
    found = next((p for p in CYCLE_PERIODS if len(shape.recent) >= 2 * p
                  and list(shape.recent)[-p:] == list(shape.recent)[-2 * p:-p]
                  and len({c for _, c, _ in list(shape.recent)[-p:]}) > 1), 0)
    if not found:
        shape.cycle_warned = ()
        return ""
    loop_steps = tuple(list(shape.recent)[-found:])
    if any(k in _WAITING for k, _, _ in loop_steps) or set(loop_steps) == set(shape.cycle_warned):
        return ""
    shape.cycle_warned = loop_steps
    shape.warnings += 1
    kinds = " → ".join(k for k, _, _ in loop_steps)
    wrongturn.record(loop.ctx, "action_cycle", turn=loop.ctx.turn, streak=found,
                     detail=f"a cycle of {found} actions ({kinds}) ran twice with the same "
                            "results — every action valid, the run not moving")
    return (f"\n[ENGINE WARNING: your last {2 * found} actions are one cycle of {found} "
            f"({kinds}) run twice, with the same results both times — the run is not moving. "
            "Do something different: change an argument, take another route, or go on with "
            "what you have.]")


def _failed(loop, shape: Shape, kind: str, call: str) -> str:
    if kind != shape.fail_kind:
        shape.fail_kind, shape.fail_calls, shape.fail_warned = kind, set(), False
    shape.fail_calls.add(call)
    if len(shape.fail_calls) < VARIED_FAILS or shape.fail_warned:
        return ""
    shape.fail_warned = True
    shape.warnings += 1
    wrongturn.record(loop.ctx, "varied_failures", turn=loop.ctx.turn,
                     streak=len(shape.fail_calls),
                     detail=f"{len(shape.fail_calls)} different {kind} calls failed in a row")
    return (f"\n[ENGINE NOTE: {len(shape.fail_calls)} different {kind} calls have now failed "
            "in a row. Stop varying the call: read the last error in full and fix what it "
            "names, or take a different route.]")
