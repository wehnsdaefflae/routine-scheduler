"""Run control plane: the abort switch, the pause gate, mid-run model switches, and the
turn-boundary message feeds (injected user messages, finished sub-workflow announcements).

Everything here runs BETWEEN turns and mutates only the loop's message list / context —
never the model call itself. control.json stays web-owned: the engine only reads it
(pause, switch_model) and reacts at the next turn boundary.
"""

from __future__ import annotations

import time

from ..config import ModelRef
from ..paths import read_json
from . import inbox
from .composer import truncate

_ABORT = {"flag": False}


def request_abort() -> None:
    _ABORT["flag"] = True


class RunAborted(Exception):
    pass


def pause_gate(loop, poll_s: float) -> None:
    """Hold the run while control.json says pause; the waiting time is credited back to
    the wall-clock budget."""
    ctx = loop.ctx
    control = ctx.root_run_dir / "control.json"
    obj = read_json(control)
    if not (isinstance(obj, dict) and obj.get("pause")):
        return
    ctx.write_status("paused")
    started = time.monotonic()
    while True:
        if loop._aborted():
            raise RunAborted()
        time.sleep(poll_s)
        obj = read_json(control)
        if not (isinstance(obj, dict) and obj.get("pause")):
            break
    ctx.credit_suspended(time.monotonic() - started)
    ctx.write_status("running")


def apply_model_switch(loop) -> None:
    """Turn-boundary: honour a mid-run model switch written to control.json by the web layer.
    Edge-triggered on the signal's `ts` so the engine never has to write control.json (which
    stays web-owned). The switch lands on the NEXT completion, since for_model re-resolves
    ctx.routine.models every turn — the model, its context size, and effort all self-correct."""
    ctx = loop.ctx
    obj = read_json(ctx.root_run_dir / "control.json")
    sw = obj.get("switch_model") if isinstance(obj, dict) else None
    if not isinstance(sw, dict) or not sw.get("ts") or sw["ts"] == loop._last_switch_ts:
        return
    loop._last_switch_ts = str(sw["ts"])
    applied = []
    for kind in ("main", "subroutine", "tool_call"):
        spec = sw.get(kind)
        if (isinstance(spec, dict) and spec.get("endpoint") in ctx.server.endpoints
                and spec.get("model")):
            ctx.routine.models[kind] = ModelRef(endpoint=str(spec["endpoint"]),
                                                model=str(spec["model"]), effort=spec.get("effort"))
            applied.append(f"{kind} → {spec['endpoint']}/{spec['model']}")
    if applied:
        note = "model switched mid-run: " + "; ".join(applied)
        ctx.transcript.event("user_injection", {"text": f"[engine] {note}", "source": "engine"})
        loop.messages.append({"role": "user", "content":
            f"ENGINE NOTE: {note}. Continue the run on the new model."})


def drain_injections(loop) -> None:
    """Feed mid-run user messages from the inbox into the conversation (root runs only)."""
    ctx = loop.ctx
    if ctx.depth > 0:
        return
    for text in inbox.drain_messages(ctx.routine.dir, loop.consumed_dir):
        ctx.transcript.event("user_injection", {"text": text})
        loop.messages.append({"role": "user",
                              "content": f"USER MESSAGE (injected mid-run):\n{text}"})


def announce_finished_subruns(loop) -> None:
    """Turn-boundary notification: children that exited since the last boundary."""
    for sub in loop.subruns.take_finished_unannounced():
        summary, _ = truncate(sub.summary, cap=4000)
        loop.messages.append({"role": "user", "content":
            f"SUB-WORKFLOW FINISHED — #{sub.n} {sub.label!r} (workflow {sub.workflow}, "
            f"status {sub.status}, {sub.ctx.turn} turns):\n{summary}"})
