"""Which process the kernel's OOM killer takes first: a RUN, never the daemon.

Every engine process raises its own `oom_score_adj` to the maximum the moment it starts
(`cli.cmd_engine_run`), before any jail exists, and everything it spawns — utils, scripts, a
`shell` command, a test suite a gate starts — inherits the value. The daemon keeps 0. When a
memory cgroup runs out, the kernel kills the highest-scoring process in it, so it now takes the
largest process of SOME run and leaves the daemon, which holds every run, alive.

WHY: the daemon was the victim. Eight times between 2026-10-22 and 2026-10-30 the engine
container's cgroup hit its 5 GiB ceiling while a release gate's browser stage ran, and the
kernel — choosing by resident size plus swap, with every adjustment at 0 — killed the daemon
(`journalctl -k`: "Memory cgroup out of memory: Killed process … (rsched)"), which ended the
container and orphaned every live run. The cause was a test suite; the cost was the fleet. A
run killed instead costs one run, which the reap resumes or reports; the daemon killed costs
all of them, and leaves nothing behind that says why (`daemon/containerbirth.py`).

Raising the value needs no privilege; lowering the daemon's below 0 would need
CAP_SYS_RESOURCE, so the ranking is made by lifting the expendable side. A jailed process
cannot do it for itself — `/proc` is read-only inside the sandbox (`sandbox._SYSTEM_RO`) — which
is why the engine does it, once, for its whole tree.
"""
from __future__ import annotations

from pathlib import Path

#: The kernel's maximum: a process at 1000 outranks any process at 0 whatever their sizes, so
#: the order between a run and the daemon never depends on how much either holds at the time.
RUN_OOM_SCORE_ADJ = 1000
_SELF = Path("/proc/self/oom_score_adj")


def mark_run_expendable(path: Path = _SELF) -> str:
    """Raise this process's OOM score to the maximum. Returns "" or what went wrong, for the
    caller to log — a run that cannot rank itself still runs; it only stays as likely a victim
    as the daemon, which is how every run ran before this existed.
    """
    try:
        path.write_text(f"{RUN_OOM_SCORE_ADJ}\n", encoding="ascii")
    except OSError as exc:
        return (f"could not raise this run's OOM score ({path}: {exc}); under memory "
                f"pressure the kernel may take the daemon instead of this run")
    return ""
