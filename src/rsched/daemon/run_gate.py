"""Bounded, model-free admission for explicitly opted-in automatic runs (F457)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path

from .. import domains, sandbox, scripts, secrets, utils_header, utils_run
from ..config import RoutineConfig, ServerConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..paths import atomic_write, atomic_write_json, read_json
from .runner_state import ActiveRun

OUTPUT_LIMIT = 16384


class GateError(Exception):
    """Admission failed: never interpret an error as permission to skip or run."""


def pending_inbox(directory: Path) -> bool:
    """Does freight wait for this routine? `msg-*.json` only — the stem the ONE writer
    produces (engine/inbox.file_message). Counting ANY file made a queued question ANSWER,
    and `paths.atomic_write`'s in-flight `.msg-….json.XXXX.tmp`, read as pending work the
    gate must be told about.
    """
    inbox = directory / "inbox"
    return inbox.is_dir() and any(inbox.glob("msg-*.json"))


def _prepare(cfg: RoutineConfig, server: ServerConfig) -> tuple[list[str], dict]:
    root = cfg.dir.resolve()
    path = scripts.script_path(root, "gate").resolve(strict=True)
    if not path.is_relative_to(root / "scripts") or not path.is_file():
        raise GateError("scripts/gate.py must be a contained regular file")
    if path.stat().st_size > 262144:
        raise GateError("gate source exceeds 256 KiB")
    source = path.read_text(encoding="utf-8")
    header = utils_header.parse_header(source)
    fs_lines = [line for line in header["doc"].splitlines()
                if line.strip().lower().startswith("fs:")]
    if len(fs_lines) > 1:
        raise GateError("duplicate gate fs declaration")
    if fs_lines:
        _, _, problems = utils_header.parse_fs(header["fs"])
        if problems:
            raise GateError("invalid gate filesystem declaration: " + "; ".join(problems))
    if header["calls"] or any(
        line.strip().lower().startswith("calls:") for line in header["doc"].splitlines()
    ):
        raise GateError("calls: is not supported for admission gates")
    if scripts.misdeclared(root, "gate") or scripts.call_problems(
        root, "gate", server.libraries_home
    ):
        raise GateError("misdeclared gate metadata or util calls")
    if header["net"] not in ("", "none", "outbound"):
        raise GateError("invalid gate net declaration")
    declared = {v.upper() for v in header["secrets"]}
    optional = {v.upper() for v in header["optional_secrets"]}
    owned = secrets.load_routine_secrets(cfg.slug)
    central = secrets.load_secrets()
    granted = {k for k in declared if k in owned or cfg.grants.get(f"secret:{k}") is True}
    available = {**central, **owned}
    missing = declared - optional - (granted & available.keys())
    if missing:
        raise GateError(
            "required gate secrets missing or unauthorized: " + ", ".join(sorted(missing))
        )
    injected = {k: available[k] for k in granted & available.keys()}
    env = utils_run.scoped_env(set(injected), injected, declared - set(injected))
    # Do not inherit credentials, Python injection, or a util dispatcher from daemon PATH.
    env = {
        k: v
        for k, v in env.items()
        if k in {"HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        or k in injected
    }
    env["PATH"] = "/usr/bin:/bin"
    python = sys.executable
    if scripts.script_deps(root, "gate"):
        py = scripts.venv_python(root)
        if not py.is_file():
            raise GateError(
                "gate dependencies require a preprovisioned .venv; no admission-time installs"
            )
        python = str(py)
    stores = domains.member_store_roots(cfg.dir.parent, cfg.domain, create=True)
    # Admission must never degrade silently, even on a permissive/off server.
    policy = sandbox.SandboxPolicy(
        mode="strict",
        own_dir=root,
        read_roots=(*cfg.fs_read_roots, *stores, *sandbox._shared_read_roots(server)),
        write_roots=(*cfg.fs_write_roots, *stores),
    )
    try:
        cmd = sandbox.wrap(
            [python, "-I", str(path)],
            policy=policy,
            libraries_home=server.libraries_home,
            net=header["net"] == "outbound",
            fs_roots=header["fs_roots"],
            fs_paths=tuple(header["fs_paths"]),
        )
    except sandbox.SandboxRefusal as exc:
        # Preserve the capability diagnosis, not the shared util-mode fallback advice.
        diagnosis = str(exc).split(" — ", 1)[0]
        raise GateError(
            f"{diagnosis}. Admission requires working strict "
            "Landlock filesystem/network isolation; "
            "enable the required kernel/LSM support, use an intentional manual run "
            "(which bypasses admission), or explicitly disable run_gate. "
            "Changing server sandbox mode cannot relax admission isolation."
        ) from None
    return cmd, env


def _bootstrap_cmd() -> list[str]:
    # -I ignores cwd/PYTHONPATH/site-user injection. Pin the already loaded source tree,
    # not an installed cli which might boot models or resolve a different deployment.
    source = str(Path(__file__).resolve().parents[2])
    code = (
        f"import sys; sys.path.insert(0, {source!r}); "
        "from rsched.daemon.run_gate import child_main; child_main()"
    )
    return [sys.executable, "-I", "-c", code]


def child_main() -> None:
    """Trusted preparation child; stdin is private configuration, never script input."""
    try:
        payload = json.load(sys.stdin)
        cfg = RoutineConfig.model_validate(payload["routine"])
        server = ServerConfig.model_validate(payload["server"])
        pending = pending_inbox(cfg.dir)
        if pending or payload.get("inbox_only"):
            sys.stdout.write(json.dumps({
                "version": 1, "decision": "run" if pending else "skip",
                "reason": "pending inbox" if pending else "inbox empty"}))
            return
        cmd, env = _prepare(cfg, server)
        os.chdir(cfg.dir)
        os.execve(cmd[0], [*cmd, json.dumps(payload["context"])], env)  # noqa: S606 — trusted command
    except Exception as exc:
        sys.stderr.write(f"gate preparation failed: {type(exc).__name__}: {exc}\n")
        raise SystemExit(1) from None


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
    *, inbox_only: bool = False,
) -> dict:
    # All filesystem, secret-store and sandbox preparation happens in a killable process.
    # Only the fields needed by preparation cross this private pipe; no model config,
    # endpoint token or secret value is serialized, put in argv, or logged.
    payload = json.dumps({
        "inbox_only": inbox_only,
        "routine": cfg.model_dump(mode="json", include={
            "slug", "dir", "grants", "domain", "fs_read_roots", "fs_write_roots"}),
        "server": server.model_dump(
            mode="json", include={"libraries_home", "routines_home", "sandbox"}),
        "context": {"version": 1, "routine": cfg.slug, "run_id": run.run_id, "reason": reason},
    }).encode()
    tasks: list[asyncio.Task] = []
    proc = None
    stdout, stderr = bytearray(), bytearray()
    spawn = None
    try:
        async with asyncio.timeout(cfg.run_gate.timeout_s):
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                *_bootstrap_cmd(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            ))
            # Shield the spawn handshake so cancellation cannot lose a live child's PID.
            proc = await asyncio.shield(spawn)
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
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "decision", "reason"}
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
        if proc is None and spawn is not None:
            proc = await spawn
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Bounded diagnostics are kept in the run, not mixed into the protocol/log.
        if not inbox_only:
            atomic_write(run.run_dir / "gate-stderr.txt", stderr.decode("utf-8", "replace"))
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
        # The final inbox check is filesystem work too: keep it off the event loop
        # and share ONE deadline across preparation, predicate and this race guard.
        try:
            async with asyncio.timeout(cfg.run_gate.timeout_s):
                result = await _execute(run, cfg, server, reason)
                meta.update(result)
                if run.user_cancel or run.cancelled:
                    raise GateError("gate aborted")
                if result["decision"] == "run":
                    return True
                inbox = await _execute(run, cfg, server, reason, inbox_only=True)
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
        atomic_write_json(run.run_dir / "gate.json", meta)
