"""WHY a run's process is gone — the one classifier, read by the daemon and by the engine.

Extracted from `runner_reap.py`, which owns everything that happens once a process stops. This
is the part of that job which decides nothing and does nothing: it takes an exit status and a
memory sample and returns one word. Every other function in the reap takes the live `runner` and
writes something; this one is a pure reading, and it has two callers in different layers — the
daemon's reap and `engine/executor.py`'s `shell` verdict, which deliberately uses the SAME
classifier rather than a second one that could drift from it.

The vocabulary is small on purpose and each member is a different investigation. It is also
`abortmark`'s vocabulary, extended there by `parent_kill`, so a word added here is a word the
health stream gains.
"""

from __future__ import annotations

from pathlib import Path

#: A SIGKILL whose peak resident memory is BELOW this fraction of the host's RAM did not run out
#: of memory, whatever rc=-9 suggests on its own. F348 sampled the peak precisely because "a peak
#: near the host's RAM is the kernel-OOM signature" — but nothing ever compared the two, so every
#: SIGKILL wore the OOM verdict (F569): a conversation reaped twice at VmHWM 61,716 kB and
#: 64,492 kB — ~60 MB — was called `oom_kill` and its resumed leg was told "likely
#: out-of-memory" as established fact. A tenth is deliberately generous: the kernel kills the
#: biggest consumer under pressure, so a true victim sits near the ceiling, not a rounding error
#: below it.
_OOM_PLAUSIBLE_FRACTION = 0.10


def _host_ram_kb() -> int | None:
    """Total host RAM in kB from /proc/meminfo, or None where it cannot be read.

    None is not evidence: without a ceiling to compare against, a peak proves nothing either
    way and the caller keeps its pre-existing verdict.
    """
    try:
        with Path("/proc/meminfo").open(encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def classify_cause(rc: int | None, *, user_cancel: bool = False,
                   vm_hwm_kb: int | None = None) -> str:
    """Name WHY a run's process is gone, from the exit status the reap already has (F480).

    The live reap read `rc` and then threw the distinction away: every death became
    `orphaned_run` with the number buried in prose, so "did anything die by signal in this
    window?" was not a question the stream could answer. The vocabulary is deliberately small
    and each member is a different investigation:

      user_abort     — a person asked for it; nothing to diagnose.
      oom_kill       — SIGKILL (rc=-9) with a peak that supports it (D99/F348/F569).
      signal_kill    — died on some other signal, or on SIGKILL with a peak far below the
                       host's RAM: a supervisor stop, a deploy, a manual kill.
      engine_crash   — exited non-zero on its own; the traceback is the lead.
      no_finish      — exited CLEANLY yet wrote no finish, which is an engine defect.
      unknown        — rc was never observed (the boot path); honest, and filterable.

    `vm_hwm_kb` is the dead engine's peak resident memory when a status write captured one.
    It only ever moves a verdict AWAY from `oom_kill`, and only when it is low enough to
    contradict it — an absent sample leaves the rc=-9 reading exactly as it was (F569).
    """
    if user_cancel:
        return "user_abort"
    if rc is None:
        return "unknown"
    if rc == -9:
        ram = _host_ram_kb()
        if vm_hwm_kb and ram and vm_hwm_kb < ram * _OOM_PLAUSIBLE_FRACTION:
            return "signal_kill"
        return "oom_kill"
    if rc < 0:
        return "signal_kill"
    return "engine_crash" if rc else "no_finish"
