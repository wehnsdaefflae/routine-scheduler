"""The leaf facts a run is tracked BY — the record, the command line, and the process probes.

Split out of `runner.py` (F393). It exists to be depended on from both sides: `runner.py` starts
and supervises processes, `runner_reap.py` handles what happens after one ends, and both need
these. Putting them here is what keeps that a hierarchy rather than a cycle — the alternative was
a deferred import, which hides a cycle instead of removing one.

Three slot POOLS, not one: cron load can never queue a chat reply, and a couple of long
fire-and-forget background jobs starve neither. A run parked on a user question releases its
slot, so waiting on a person costs nothing.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import ServerConfig
from ..ids import now_iso
from ..paths import config_file, read_json
from ..registry import homes_fingerprint

# WARNING/ERROR/CRITICAL/traceback markers in an engine subprocess's stderr. The engine's
# stdout is DEVNULL and its stderr is only surfaced by _reap on a CRASH — so a non-fatal
# diagnostic logged by a cleanly-finishing run (e.g. the util-stats snapshot write breadcrumb,
# F97) was silently dropped. _notable_stderr lets _reap re-emit just those lines.
_NOTABLE_RE = re.compile(r"\b(?:WARNING|ERROR|CRITICAL)\b|Traceback \(most recent call last\)")

def _stranded_user_messages(routine_dir: Path) -> bool:
    """An unconsumed USER message is waiting in the dir's inbox.

    USER_MESSAGE_VIAS is the injection channels that count as "a user is talking to this run"
    for the post-finish sweep (R108/F268): the conversation composer and the run page.
    Everything else that lands in an inbox — report deliveries, trigger events, one-shot
    provenance, background results, audit feedback — has its own wake policy and must never
    re-open a finished run from the reap. The tuple lives with the engine's inbox
    (`engine.inbox.USER_MESSAGE_VIAS`) because the resume-boot drain (F359) keys on the same
    channels, so wake policy and consumption policy stay ONE vocabulary.
    """
    from ..engine.inbox import USER_MESSAGE_VIAS
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return False
    for p in inbox.iterdir():
        if not p.is_file() or p.name.startswith("answer-"):
            continue
        obj = read_json(p)
        if (isinstance(obj, dict) and obj.get("text")
                and str(obj.get("via") or "") in USER_MESSAGE_VIAS):
            return True
    return False

def _notable_stderr(stderr: bytes, *, max_lines: int = 12, max_chars: int = 800) -> str:
    """A compact tail of the WARNING/ERROR/CRITICAL/traceback lines in captured stderr, or
    "" when the subprocess logged nothing notable. Keeps only the tail so a chatty run can
    never flood the daemon log, while a real, repeating failure stays visible every run.
    """
    hits = [ln.strip() for ln in stderr.decode("utf-8", "replace").splitlines()
            if _NOTABLE_RE.search(ln)]
    return " | ".join(hits[-max_lines:])[-max_chars:] if hits else ""

KILL_GRACE_S = 10

STATUS_POLL_S = 2.0

# Conversations (interactive replies) get their own slot pool: cron load can never queue a
# chat reply, and a long agentic reply never starves the schedule.
INTERACTIVE_SLOTS = 3

# Detached background tasks (dirs under background_home) get a THIRD pool so a couple of
# long fire-and-forget jobs starve neither the schedule nor chat replies.
BACKGROUND_SLOTS = 2

def engine_cmd(server: ServerConfig, target: str, run_ts: str, *,
               resume: bool = False) -> list[str]:
    """The argv for one run's engine subprocess. `target` is a routine slug (resolved under
    routines_home) or a directory path — conversations live under their own home, so the
    runner always passes cfg.dir.

    The child is a FRESH interpreter that inherits none of this process's configuration, so
    it is told WHICH config file to load (`--config`) and which run homes that file must
    resolve to (`--homes`); `engine-run` requires both and refuses a mismatch. Nothing is
    left to default — a spawner whose config was never loaded from a file is refused HERE,
    before a process exists, because the child would otherwise fall back to
    `~/.config/routine-scheduler/config.yaml` and execute the routine against the PRODUCTION
    instance's homes, endpoints and money. That is not hypothetical: F394 (2026-08-27) is
    two full runs of a tmp-homed test fixture against production, for want of this refusal.
    """
    if server.source is None:
        raise RuntimeError(
            "engine_cmd: this ServerConfig was never loaded from a file (source is None), "
            "so the engine subprocess has nothing to point at and would fall back to "
            f"{config_file()} — the production instance. Load the config with "
            "load_server_config(<path>), or stub the spawn (tests do).")
    cmd = [sys.executable, "-m", "rsched.cli", "engine-run", target, "--run-ts", run_ts,
           "--config", str(server.source), "--homes", homes_fingerprint(server)]
    if resume:
        cmd.append("--resume")
    return cmd

def _queued_status(run_id: str, ts: str, prior: object = None) -> dict:
    """The minimal 'queued' status.json written the moment a run is (re)armed, before its
    engine subprocess boots. On a RESUME the run dir is reused and status.json still holds the
    prior leg's cumulative telemetry (the `utils` histogram + the integer counters); pass that
    dict as `prior` so this write CARRIES IT FORWARD instead of clobbering it. Otherwise the
    boot-time prior_counters reseed (F131/F132) reads an already-wiped file and a finish->reopen
    loses the pre-finish leg's util calls and counters (F140). A fresh run passes prior=None.
    """
    status = dict(prior) if isinstance(prior, dict) else {}
    status.update({"run_id": run_id, "state": "queued", "started": ts,
                   "updated": now_iso(), "turn": 0, "question": None,
                   "usage": {"in": 0, "out": 0}})
    return status

@dataclass
class ActiveRun:
    """A run the daemon tracks: queued for a slot, running as a subprocess, or parked on
    a user question (a parked run releases its slot — `holds_slot`).
    """

    slug: str
    run_id: str
    run_ts: str
    run_dir: Path
    proc: asyncio.subprocess.Process | None = None  # None while queued for a slot
    holds_slot: bool = False
    sem: asyncio.Semaphore | None = None  # the pool this run draws from (cron vs interactive)
    background: bool = False  # a detached task — excluded from the self-update drain gate
    cancelled: bool = False   # aborted while still QUEUED — the supervisor spawns nothing
    # user-requested abort of a RUNNING process (F188): the kill leaves no engine finish, so
    # the reap closes the run out itself — and logs `run_canceled` rather than `orphaned_run`,
    # because a deliberate cancel must not masquerade as a crash in the health stream.
    user_cancel: bool = False

def _last_vm_hwm_kb(run_dir: Path) -> int | None:
    """The dead engine's peak resident memory, from its LAST status.json write (the
    engine samples /proc/self/status VmHWM into every status update — F348).
    """
    st = read_json(run_dir / "status.json")
    if not isinstance(st, dict):
        return None
    try:
        val = st.get("vm_hwm_kb")
        return int(val) if val else None
    except (TypeError, ValueError):
        return None

def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, owned by another uid — alive (EPERM is not ESRCH)
    return True

async def abort_process(pid: int | None) -> bool:
    """SIGTERM the engine's process group; SIGKILL stragglers after the grace period.
    (F283: the run-dir/run-id params every caller dutifully passed were never used —
    close-out attribution is the CALLER's job, via _close_out/_reap.)
    """
    if not pid or not _pid_alive(pid):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    for _ in range(int(KILL_GRACE_S / 0.5)):
        await asyncio.sleep(0.5)
        if not _pid_alive(pid):
            return True
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return True
