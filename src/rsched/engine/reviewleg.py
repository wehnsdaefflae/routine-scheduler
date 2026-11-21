"""The REVIEW LEG — a routine run that had something to learn from looks back once, at its end.

The learning a run leaves behind used to happen INSIDE its task: memory upkeep, the D200 recipe
ask, the reminder a run sets itself — each a turn the task paid for, and the overhead measurement
(engine/overhead.py) counts every one of them against it. Hermes Agent (Nous Research) moved that
work out of the conversation: after a turn it forks the agent ONTO THE SAME CACHED PREFIX, hands
the fork a review prompt and a narrow tool allowlist, and lets it write memory and skills while
the main conversation goes on. This is that loop, fitted to a scheduled run:

- **When.** Not on a counter — every run here is scheduled, and Hermes turns its own review OFF
  for cron at ~30K tokens an event. It runs when the run's OWN RECORD says it had something to
  learn from (`triggers`): actions that failed, an action a caution held, replies the schema
  refused, a repeat or a cycle the engine warned about, a person writing in mid-run, a `partial`
  or authored `failed` ending, or notes no run has read in `notereads.UNREAD_RUNS` runs. And not
  when the run kept its own notes this time (Hermes' back-off: a foreground memory write resets
  the counter), was aborted, spent a budget, or is a child, a conversation, a detached task, a
  narrow revise leg or an admin leg. The instance switch is `ServerConfig.review_leg`.
- **Where.** In the close-out every ending shares (`loopend.finish_run`), BEFORE the finish event,
  the ledger entry and the autocommit — so the notes it writes are committed with the run that
  learned them, its summary line rides the run's result, and a RECIPE REVISION it names joins the
  task's own lines on ONE D200 card. The same message list, the same model and the same action
  schema: the provider serves the whole prefix from cache (the schema is part of that prefix, so
  the narrow allowlist is enforced at VALIDATION, `loop.allowed_tools`, never by changing what is
  sent — Hermes' reason for the same choice).
- **What it may do.** `LEG_KINDS` — read notes, search past runs, read files, write notes,
  finish — at most `REVIEW_TURNS` turns and `REVIEW_MAX_S` seconds, stopping at once on an abort
  or a spent budget. Two guards Hermes measured it needed: a note that exists may be revised only
  after this leg READ it (read-before-write), and a note may be deleted only once it was read AND
  another note was written this leg — folding it in — never as a prune on its own.
- **What it costs the record.** Every event it writes carries `review_leg`, and its framing note
  opens the span, so `overhead`/`frictions` leave it out of the TASK's measurement; its spend is
  the run's (`ctx.add_usage`), and `ctx.review` records what it did for the usage row's
  `quality.review`. A leg that fails for any reason costs the run nothing but the leg.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ..endpoints.base import EndpointError
from . import (
    actionroute,
    enginenote,
    mediaops,
    notereads,
    obstails,
    recipeproposal,
    remind,
    reviewnote,
    reviseleg,
    sidefields,
)
from .completion import next_action
from .control import RunAborted
from .loopconst import POLL_S
from .observations import is_failure, live_observation
from .replyactions import unexecuted_row
from .run_context import work_dir

log = logging.getLogger(__name__)

#: What the leg may emit, before the routine's own `tools:` narrowing.
LEG_KINDS = frozenset({"memory_read", "memory_write", "read_file", "finish"})

#: The leg's backstops — never a pace.
REVIEW_TURNS = 6
REVIEW_MAX_S = 300

#: A run shorter than this had too little to look back on.
MIN_TURNS = 6

#: Failed actions, refused replies and long-unread notes that make a reason by themselves.
FAILED_MIN = 2
REJECTED_MIN = 2
UNREAD_MIN = 3

#: Why a reply's later actions did not run in this leg.
CHAIN_REASON = "the review leg runs one action per reply"


def _kinds(loop) -> set[str]:
    allowed = getattr(loop, "allowed_tools", None)
    return set(LEG_KINDS) if allowed is None else (set(LEG_KINDS) & set(allowed)) | {"finish"}


def _excluded(loop, status: str, *, authored: bool, aborted: bool) -> bool:
    ctx = loop.ctx
    return bool(
        aborted or status == "aborted" or (status == "failed" and not authored)
        or not getattr(ctx.server, "review_leg", False)
        or bool(os.environ.get("RSCHED_SKIP_REVIEW_LEG"))   # the test suite's knob (conftest)
        or not recipeproposal.asks_for_revisions(ctx)   # depth 0, a routine, not detached
        or reviseleg.active(loop) or getattr(loop, "admin_leg", False)
        or ctx.turn < MIN_TURNS or getattr(loop, "_finish_reserved", False)
        or ctx.budget_spent()
        or "memory_write" in getattr(loop, "executed_kinds", set())
        or not (_kinds(loop) - {"finish"}))


def triggers(loop, status: str, *, authored: bool, aborted: bool) -> list[str]:
    """Why this run gets a review leg, in words for its note — [] when it does not."""
    if _excluded(loop, status, authored=authored, aborted=aborted):
        return []
    ctx = loop.ctx
    reasons: list[str] = []
    if (failed := sum((getattr(loop, "failures", None) or {}).values())) >= FAILED_MIN:
        reasons.append(f"{failed} of its actions failed")
    if ctx.holds:
        reasons.append(f"{ctx.holds} action(s) were held by a caution")
    if ctx.schema_retries >= REJECTED_MIN:
        reasons.append(f"{ctx.schema_retries} of its replies were refused")
    shape = getattr(loop, "shape", None)
    if getattr(loop, "_sheds", 0) or (shape is not None and shape.warnings):
        reasons.append("the engine warned it was going round in circles")
    if ctx.interventions or ctx.user_replies:
        reasons.append("a person wrote to it while it worked")
    if status in ("partial", "failed"):
        reasons.append(f"it finished {status}")
    if len(notereads.unread(Path(ctx.routine.dir) / ".memory")) >= UNREAD_MIN:
        reasons.append(f"some notes have gone unread for {notereads.UNREAD_RUNS} runs")
    return reasons


def maybe_run(loop, status: str, summary: str, *, authored: bool, aborted: bool) -> str:
    """From `loopend.finish_run`: run the leg when the run earned one; return what the run's
    summary gains — the leg's line, and its RECIPE REVISION lines for the one D200 card.
    """
    reasons = triggers(loop, status, authored=authored, aborted=aborted)
    if not reasons:
        return ""
    ctx = loop.ctx
    rec: dict = {"reasons": reasons, "turns": 0, "wrote": [], "deleted": [], "summary": ""}
    saved = (loop.allowed_tools, loop.assists)
    try:
        _drive(loop, status, rec)
    except (RunAborted, EndpointError) as exc:
        rec["stopped"] = type(exc).__name__
    except Exception as exc:   # the leg is extra: it must never cost the run its close-out
        log.warning("review leg failed for %s", ctx.run_id, exc_info=True)
        rec["stopped"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        loop.allowed_tools, loop.assists = saved
        loop.review_leg = False
    ctx.review = {k: v for k, v in rec.items() if k != "summary"}
    revisions = [line for line in recipeproposal.extract(rec["summary"])
                 if line not in summary]
    return reviewnote.result_line(rec) + "".join(
        f"\n{recipeproposal.MARKER} {line}" for line in revisions)


def _drive(loop, status: str, rec: dict) -> None:
    ctx = loop.ctx
    loop.allowed_tools = _kinds(loop)
    loop.assists = []
    loop.review_leg = True
    mem_dir = work_dir(ctx) / ".memory"
    enginenote.append(loop, reviewnote.note(
        status, rec["reasons"], kinds=sorted(loop.allowed_tools), turns=REVIEW_TURNS,
        unread=notereads.unread(Path(ctx.routine.dir) / ".memory"),
        revisions=recipeproposal.asks_for_revisions(ctx),
        reminders=bool(ctx.grants and ctx.grants.reminders_on)), review_leg=True)
    read: set[str] = set()
    started = time.monotonic()
    while (rec["turns"] < REVIEW_TURNS and time.monotonic() - started < REVIEW_MAX_S
           and not loop._aborted() and not ctx.budget_spent()):
        action, usage = next_action(loop)
        ctx.add_usage(usage)
        if action is None:
            rec["stopped"] = "no valid action"
            return
        queued = [a for a in loop.chain_queue if isinstance(a, dict)]
        loop.chain_queue, loop.unexecuted = [], []
        action = {k: v for k, v in action.items() if k != "background"}
        ctx.turn += 1
        rec["turns"] += 1
        ctx.transcript.event("assistant_action", dict(action), turn=ctx.turn, usage=usage,
                             review_leg=True)
        sidefields.land(loop, action)
        loop.messages.append({"role": "assistant",
                              "content": json.dumps(action, ensure_ascii=False)})
        if action["kind"] == "finish":
            rec["summary"] = str(action.get("summary") or "").strip()
            return
        obs = _gate(action, mem_dir, read, rec) or actionroute.dispatch_action(loop, action, ctx)
        if queued:
            obs = {**obs, "not_executed": [unexecuted_row(a) for a in queued],
                   "chain_stopped": CHAIN_REASON}
        _account(action, obs, read, rec)
        tails = remind.apply_ops(loop, action, poll_s=POLL_S)
        ctx.transcript.event("observation", obstails.record(mediaops.without_bytes(obs), tails),
                             turn=ctx.turn, review_leg=True)
        loop.messages.append({"role": "user", "content": live_observation(obs, ctx) + tails})
    rec["stopped"] = "backstop"


def _gate(action: dict, mem_dir: Path, read: set[str], rec: dict) -> dict | None:
    """The leg's two note guards — an error observation, or None to dispatch."""
    if action.get("kind") != "memory_write":
        return None
    name = str(action.get("name") or "")
    if action.get("delete"):
        if name in read and any(w != name for w in rec["wrote"]):
            return None
        return {"kind": "memory_write", "name": name, "error":
                "the review leg deletes a note only to FOLD it into another: memory_read it, "
                "write the note that now carries what it held, then delete it"}
    if (mem_dir / f"{name}.md").is_file() and name not in read:
        return {"kind": "memory_write", "name": name, "error":
                f"note {name!r} exists — memory_read it in this leg before you revise it"}
    return None


def _account(action: dict, obs: dict, read: set[str], rec: dict) -> None:
    if is_failure(obs):
        return
    name = str(action.get("name") or "")
    if action.get("kind") == "memory_read" and name and obs.get("content") is not None:
        read.add(name)
    elif action.get("kind") == "memory_write":
        (rec["deleted"] if obs.get("deleted") else rec["wrote"]).append(name)
