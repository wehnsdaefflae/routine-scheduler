"""RUNNING one ad-hoc shell command — the `shell` action's subprocess, jail and output cap.

The third callable kind, beside `utils_run` (the shared library) and `scripts` (the routine's
own helpers), and deliberately the same shape as both: build the env, wrap the command in the
Landlock jail `sandbox.wrap` composes from the RUN's granted roots, run it in its own process
group with file-backed capture, cap what comes back.

The jail is the whole point. `shell` was a reserved util until 0.287.0, and a util subprocess
is jailed to the run's granted roots intersected with the util's own `fs:` declaration — the
shell util declared `fs: roots` + `net: outbound`, the widest terms available, so its
intersection term was a no-op and its effective bound was exactly the run's granted roots.
This module reproduces that bound (`fs_roots=True`, `net=True`), so the move to an action kind
changes what can GENERATE the call, never what the call can reach. Weakening either flag would
turn a gating improvement into a sandbox regression.

What this module owns is the jail TERMS; the process itself is `utils_run.run_jailed`, shared
with the util and script kinds — including the bounded read that stops a command printing
gigabytes from being materialized in the daemon's memory, the end of a command whose run is
aborted, and the exit codes a deadline and an abort report (`Jailed.exit_code`).

Secrets: NONE. The old util declared no `secrets:` header, so `utils_run.scoped_env` injected
nothing and scrubbed every store key out of the inherited environment; `scoped_env(set())` here
is that same call. A command that needs a credential is a util with a `secrets:` line, not a
shell one-liner.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import sandbox
from .utils_run import run_jailed, scoped_env

SHELL_DEFAULT_TIMEOUT_S = 120


def run_shell(command: str, *, policy: sandbox.SandboxPolicy, libraries_home: Path,
              cwd: Path, timeout: int = SHELL_DEFAULT_TIMEOUT_S,
              aborted: Callable[[], bool] | None = None,
              cancelled: Callable[[], bool] | None = None) -> dict:
    """Run ONE command through `bash -c` inside the run's jail. Returns
    {exit, stdout, stderr, truncated, timed_out, aborted, cancelled} — never raises for the
    command's own failure, which is data the run must see, not an engine error. `aborted` is
    the run's own abort check: the command ends with its run (`utils_run.run_jailed`).
    `cancelled` is the per-call one (F586/D160): a person stopped THIS command and the run
    carries on, which the dict reports as its own flag beside `timed_out` and `aborted`
    because the three endings call for three different next moves.

    The library root is on PATH (and `GLOBAL_UTILS_HOME` is set) because it always was: the
    jail mounts the library read-only for every callable kind, so a command could reach `gu`
    by absolute path regardless — hiding the name would cost legibility and buy nothing.
    """
    if not command.strip():
        return {"exit": 2, "stdout": "", "stderr": "empty command", "truncated": False,
                "timed_out": False, "aborted": False, "cancelled": False}
    env = scoped_env(set())          # no declared secrets: the store is scrubbed wholesale
    env["PATH"] = f"{libraries_home}:{env.get('PATH', '')}"
    env["GLOBAL_UTILS_HOME"] = str(libraries_home)
    try:
        cmd = sandbox.wrap(["bash", "-c", command], policy=policy,
                           libraries_home=libraries_home, net=True, fs_roots=True, fs_paths=())
    except sandbox.SandboxRefusal as exc:
        return {"exit": 2, "stdout": "", "stderr": str(exc), "truncated": False,
                "timed_out": False, "aborted": False, "cancelled": False}
    res = run_jailed(cmd, env=env, cwd=cwd, timeout=timeout, label="the command",
                     config_seal=policy.own_dir, aborted=aborted, cancelled=cancelled)
    return {"exit": res.exit_code,
            "stdout": res.stdout, "stderr": res.stderr,
            "truncated": res.stdout.capture_truncated or res.stderr.capture_truncated,
            "timed_out": res.timed_out, "aborted": res.aborted,
            "cancelled": res.cancelled}
