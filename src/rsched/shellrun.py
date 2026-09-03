"""RUNNING one ad-hoc shell command — the `shell` action's subprocess, jail and output cap.

The third callable kind, beside `utils_run` (the shared library) and `scripts` (the routine's
own helpers), and deliberately the same shape as both: build the env, wrap the command in the
Landlock jail `sandbox.wrap` composes from the RUN's granted roots, run it in its own process
group with file-backed capture, cap what comes back.

The jail is the whole point. `shell` was a reserved util until 0.287.0, and a util subprocess
is jailed to the run's granted roots intersected with the util's own `fs:` declaration — the
shell util declared `fs: roots` + `net: outbound`, the widest terms available, so its
intersection term was a no-op and its effective bound was exactly the run's granted roots.
This runner reproduces that bound (`fs_roots=True`, `net=True`), so the move to an action kind
changes what can GENERATE the call, never what the call can reach. Weakening either flag would
turn a gating improvement into a sandbox regression.

Secrets: NONE. The old util declared no `secrets:` header, so `utils_run.scoped_env` injected
nothing and scrubbed every store key out of the inherited environment; `scoped_env(set())` here
is that same call. A command that needs a credential is a util with a `secrets:` line, not a
shell one-liner.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from . import sandbox
from .utils_run import scoped_env

SHELL_DEFAULT_TIMEOUT_S = 120
TIMEOUT_EXIT = 124                # the shell convention (`timeout(1)`), kept from the util
# Per-stream cap, head+tail — inherited verbatim from the retired util, whose docstring named
# the reason: a chatty command must never be able to flood a transcript. The observation layer
# caps again (much harder) and spills the full text to `.util_outputs/`, so this is the size of
# what SURVIVES a shell call, not what the reader is shown.
STREAM_CAP = 64_000


def capped(text: str) -> tuple[str, bool]:
    """`(text, was_capped)` at STREAM_CAP, keeping head and tail around an elision marker."""
    if len(text) <= STREAM_CAP:
        return text, False
    head = int(STREAM_CAP * 0.7)
    tail = STREAM_CAP - head
    return (text[:head] + f"\n[... {len(text) - STREAM_CAP} chars omitted (head+tail kept) ...]\n"
            + text[-tail:]), True


def run_shell(command: str, *, policy: sandbox.SandboxPolicy, libraries_home: Path,
              cwd: Path, timeout: int = SHELL_DEFAULT_TIMEOUT_S) -> dict:
    """Run ONE command through `bash -c` inside the run's jail. Returns
    {exit, stdout, stderr, truncated, timed_out} — never raises for the command's own failure,
    which is data the run must see, not an engine error.

    The library root is on PATH (and `GLOBAL_UTILS_HOME` is set) because it always was: the
    jail mounts the library read-only for every callable kind, so a command could reach `gu`
    by absolute path regardless — hiding the name would cost legibility and buy nothing.
    """
    if not command.strip():
        return {"exit": 2, "stdout": "", "stderr": "empty command", "truncated": False,
                "timed_out": False}
    env = scoped_env(set())          # no declared secrets: the store is scrubbed wholesale
    env["PATH"] = f"{libraries_home}:{env.get('PATH', '')}"
    env["GLOBAL_UTILS_HOME"] = str(libraries_home)
    try:
        cmd = sandbox.wrap(["bash", "-c", command], policy=policy,
                           libraries_home=libraries_home, net=True, fs_roots=True, fs_paths=())
    except sandbox.SandboxRefusal as exc:
        return {"exit": 2, "stdout": "", "stderr": str(exc), "truncated": False,
                "timed_out": False}
    # Own process GROUP + spool files, for the reasons utils_run documents: a command that
    # backgrounds a child survives a plain timeout and holds the pipes open forever, and a
    # command that prints gigabytes must not be buffered in the daemon's memory.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out_f, \
            tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err_f:
        try:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f,
                                    stdin=subprocess.DEVNULL, text=True, env=env,
                                    cwd=str(cwd), start_new_session=True)
        except OSError as exc:
            return {"exit": 2, "stdout": "", "stderr": f"could not run the command: {exc}",
                    "truncated": False, "timed_out": False}
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
        # Keep whatever was printed BEFORE the kill: a command that hung after logging why
        # it hung would otherwise lose exactly the material that explains the hang.
        out_f.seek(0)
        err_f.seek(0)
        stdout, out_trunc = capped(out_f.read())
        stderr, err_trunc = capped(err_f.read())
    if timed_out:
        note = f"[timed out after {timeout}s — the process group was killed]"
        stderr = f"{stderr}\n{note}" if stderr else note
    return {"exit": TIMEOUT_EXIT if timed_out else proc.returncode,
            "stdout": stdout, "stderr": stderr,
            "truncated": out_trunc or err_trunc, "timed_out": timed_out}
