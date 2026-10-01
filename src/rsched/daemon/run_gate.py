"""Bounded, model-free admission for explicitly opted-in automatic runs (F457).

A gate is a list of CHECKS (`run_gate.checks`, answered by `rsched/gatekit/` in one jail)
plus, optionally, the routine's own `scripts/admit.py` (the `script` check, in a jail built from
its header). The fire becomes a run when ANY of them reports work; it is skipped only when every
one of them answered "no work". Preparation — the pending-inbox shortcut, the last-ok baseline,
both jails — is `daemon/gate_prepare.py`; this module owns the deadline, the process group, the
protocol and what a decision does to the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path

from ..config import RoutineConfig, ServerConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..paths import atomic_write, atomic_write_json, read_json
from .gate_prepare import GateError
from .runner_state import ActiveRun

__all__ = ["GateError", "admit", "terminal"]

OUTPUT_LIMIT = 16384


def _bootstrap_cmd() -> list[str]:
    # -I ignores cwd/PYTHONPATH/site-user injection. Pin the already loaded source tree,
    # not an installed cli which might boot models or resolve a different deployment.
    source = str(Path(__file__).resolve().parents[2])
    code = (
        f"import sys; sys.path.insert(0, {source!r}); "
        "from rsched.daemon.gate_prepare import child_main; child_main()"
    )
    return [sys.executable, "-I", "-c", code]


class _GateProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    """The stream protocol `create_subprocess_exec` uses, plus the one moment it hides: the
    gate PROCESS exiting.

    `Process.wait()` resolves only once every pipe has closed as well, and a descendant that
    left the gate's group (`setsid`) still holds the stdout/stderr it inherited after the group
    is killed: the gate, and the concurrency slot it holds, outlived its deadline for as long
    as that descendant lived (30 s measured under a 2 s deadline). `exited` is the process
    alone; closing the transport then lets go of whatever such a descendant still holds.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(limit=2 ** 16, loop=loop)   # asyncio's own default stream limit
        self.exited: asyncio.Future[None] = loop.create_future()

    def process_exited(self) -> None:
        super().process_exited()
        if not self.exited.done():
            self.exited.set_result(None)


async def _read(stream: asyncio.StreamReader | None, data: bytearray) -> None:
    if stream is None:
        raise GateError("gate output pipe unavailable")
    while chunk := await stream.read(4096):
        remaining = OUTPUT_LIMIT - len(data)
        data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            raise GateError("gate output exceeded 16 KiB per stream")


async def _execute(
    run: ActiveRun, cfg: RoutineConfig, server: ServerConfig, reason: str,
    *, mode: str = "script",
) -> dict:
    # All filesystem, secret-store and sandbox preparation happens in a killable process.
    # Only the fields needed by preparation cross this private pipe; no model config,
    # endpoint token or secret value is serialized, put in argv, or logged.
    payload = json.dumps({
        "mode": mode,
        "routine": cfg.model_dump(mode="json", include={
            "slug", "dir", "grants", "fs_read_roots", "fs_write_roots",
            "run_gate"}),
        "server": server.model_dump(
            mode="json", include={"libraries_home", "routines_home", "sandbox"}),
        "context": {"version": 1, "routine": cfg.slug, "run_id": run.run_id, "reason": reason},
    }).encode()
    tasks: list[asyncio.Task] = []
    stdout, stderr = bytearray(), bytearray()
    loop = asyncio.get_running_loop()
    spawn: asyncio.Task[tuple[asyncio.SubprocessTransport, _GateProtocol]] | None = None
    child: tuple[asyncio.SubprocessTransport, _GateProtocol] | None = None
    try:
        async with asyncio.timeout(cfg.run_gate.timeout_s):
            spawn = asyncio.create_task(loop.subprocess_exec(
                lambda: _GateProtocol(loop), *_bootstrap_cmd(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
            ))
            # Shield the spawn handshake so cancellation cannot lose a live child's PID.
            child = await asyncio.shield(spawn)
            proc = asyncio.subprocess.Process(*child, loop)
            run.proc = proc
            if run.user_cancel or run.cancelled:
                raise GateError("gate aborted")
            if proc.stdin is None:
                raise GateError("gate configuration pipe unavailable")
            tasks = [asyncio.create_task(_read(proc.stdout, stdout)),
                     asyncio.create_task(_read(proc.stderr, stderr)),
                     asyncio.create_task(proc.wait())]
            proc.stdin.write(payload)
            await proc.stdin.drain()
            proc.stdin.close()
            await asyncio.gather(*tasks)
            if run.user_cancel or run.cancelled:
                raise GateError("gate aborted")
            if proc.returncode:
                raise GateError(f"gate exited rc={proc.returncode}")
            value = json.loads(stdout.decode("utf-8"))
            allowed = {"version", "decision", "reason"} | (
                {"checks"} if mode == "checks" else set())
            if (
                not isinstance(value, dict)
                or not {"version", "decision", "reason"} <= set(value) <= allowed
                or type(value["version"]) is not int
                or value["version"] != 1
                or value["decision"] not in ("run", "skip")
                or not isinstance(value["reason"], str)
                or not 1 <= len(value["reason"].strip()) <= 1000
            ):
                raise GateError(
                    "invalid gate protocol (expected version 1 run/skip and bounded reason)")
            return value
    except TimeoutError:
        raise GateError(
            f"gate deadline exceeded ({cfg.run_gate.timeout_s}s, including preparation)") from None
    finally:
        if child is None and spawn is not None:
            child = await spawn
        if child is not None:
            transport, protocol = child
            with contextlib.suppress(ProcessLookupError):
                os.killpg(transport.get_pid(), signal.SIGKILL)
            await protocol.exited     # the process — not its pipes (_GateProtocol)
            transport.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Bounded diagnostics are kept in the run, not mixed into the protocol/log.
        if mode != "inbox" and stderr:
            with contextlib.suppress(OSError), \
                    (run.run_dir / "gate-stderr.txt").open("a", encoding="utf-8") as fh:
                fh.write(f"--- {mode}\n" + stderr.decode("utf-8", "replace"))
        run.proc = None


def _log_gate_exit(run: ActiveRun, cfg: RoutineConfig, server: ServerConfig, event: str,
                   detail: str, *, cause: str | None) -> None:
    """Put a gate failure in the health stream, where every other dead run already is.

    A gate that raises writes a `failed` run and starts no engine — so the engine's own
    `run_failed` never fires, and the reap sees a state that is already terminal and emits
    nothing either. The only durable trace was one line inside a lane chain's
    `lane_chain_done` detail. A withdrawn secret or a kernel that dropped Landlock fails
    every scheduled fire of a gated routine forever, in preparation, before turn 0 — and the
    audit's one filter is exactly this event.
    """
    log_health_event(server.routines_home, event, routine=cfg.slug, run_id=run.run_id,
                     detail=detail + " - the admission gate ended the run before turn 0, "
                                     "so no engine ever started",
                     cause=cause)


def terminal(run: ActiveRun, state: str, outcome: str, detail: str) -> None:
    raw = read_json(run.run_dir / "status.json", {})
    status = raw if isinstance(raw, dict) else {"run_id": run.run_id}
    status.update(
        state=state,
        outcome=outcome,
        summary=detail,
        updated=now_iso(),
        question=None,
    )
    atomic_write_json(run.run_dir / "status.json", status)
    atomic_write(run.run_dir / "result.md", detail + "\n")


async def _decide(run: ActiveRun, cfg: RoutineConfig, server: ServerConfig, reason: str,
                  meta: dict) -> dict:
    """Run the declarative checks, then the custom script, merging their answers into
    `meta` (what gate.json records). Work from either is a run; a skip needs both to agree.

    The checks' fingerprints are recorded whatever the decision, because an ADMITTED run's
    gate.json is the baseline the next fire compares against — a skip records them too, so the
    Test button and a reader of gate.json see what the source looked like.
    """
    checks = cfg.run_gate.checks
    declarative = [c for c in checks if c.get("kind") != "script"]
    wants_script = any(c.get("kind") == "script" for c in checks)
    decisions: list[dict] = []
    if declarative:
        res = await _execute(run, cfg, server, reason, mode="checks")
        meta["checks"] = res.get("checks") or []
        prints = {r["id"]: r["fingerprint"] for r in meta["checks"] if r.get("fingerprint")}
        if prints:
            meta["fingerprints"] = prints
        decisions.append(res)
    if wants_script and not (decisions and decisions[-1]["decision"] == "run"):
        res = await _execute(run, cfg, server, reason, mode="script")
        meta.setdefault("checks", []).append(
            {"id": "script", "kind": "script", "work": res["decision"] == "run",
             "reason": res["reason"][:300]})
        decisions.append(res)
    if not decisions:
        raise GateError("the gate is enabled but lists no checks")
    run_answers = [d for d in decisions if d["decision"] == "run"]
    chosen = run_answers[0] if run_answers else {
        "decision": "skip", "reason": " · ".join(d["reason"] for d in decisions)[:1000]}
    meta.update(decision=chosen["decision"], reason=chosen["reason"])
    return {"version": 1, "decision": chosen["decision"], "reason": chosen["reason"]}


async def admit(
    run: ActiveRun, cfg: RoutineConfig, server: ServerConfig, reason: str, resume: bool
) -> bool:
    """Return true only for bypass or explicit admission; persist every enabled-gate decision."""
    if run.user_cancel or run.cancelled:
        return False
    if not cfg.run_gate.enabled:
        return True
    meta: dict = {"version": 1, "fire_reason": reason, "run_id": run.run_id}
    try:
        bypass = (
            "resume"
            if resume
            else "scope"
            if reason not in {"schedule", "catchup", "lane"} or cfg.kind or run.background
            else ""
        )
        if bypass:
            meta.update(decision="bypass", reason=bypass)
            return True
        # ONE deadline across preparation, every predicate and the inbox race guard.
        try:
            async with asyncio.timeout(cfg.run_gate.timeout_s):
                result = await _decide(run, cfg, server, reason, meta)
                if run.user_cancel or run.cancelled:
                    raise GateError("gate aborted")
                if result["decision"] == "run":
                    return True
                inbox = await _execute(run, cfg, server, reason, mode="inbox")
                if run.user_cancel or run.cancelled:
                    raise GateError("gate aborted")
                if inbox["decision"] == "run":
                    meta.update(decision="bypass", reason="inbox arrived during gate")
                    return True
        except TimeoutError:
            raise GateError(
                f"gate deadline exceeded ({cfg.run_gate.timeout_s}s, including preparation)"
            ) from None
        terminal(run, "finished", "skipped", "Run gate skipped: " + result["reason"])
        return False
    except asyncio.CancelledError:
        terminal(run, "aborted", "failed", "Run gate cancelled")
        meta.update(decision="error", reason="cancelled")
        _log_gate_exit(run, cfg, server, "run_canceled", "Run gate cancelled",
                       cause="user_abort" if run.user_cancel else "unknown")
        raise
    except Exception as exc:
        detail = "Run gate failed: " + str(exc)[:1000]
        aborted = bool(run.user_cancel or run.cancelled)
        terminal(run, "aborted" if aborted else "failed", "failed", detail)
        meta.update(decision="error", reason=detail)
        _log_gate_exit(run, cfg, server, "run_canceled" if aborted else "run_failed", detail,
                       cause="user_abort" if aborted else None)
        return False
    finally:
        # A resumed run is the same run its gate already admitted: its record of that admission
        # is kept, never replaced by "resume" (a follow-up once erased a lane fire's verdict).
        if meta.get("reason") != "resume" or not (run.run_dir / "gate.json").exists():
            atomic_write_json(run.run_dir / "gate.json", meta)
