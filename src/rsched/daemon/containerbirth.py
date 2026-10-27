"""Was this CONTAINER recreated under a live run? — the breadcrumb an orphan's cause needs.

Three kills in two days (2026-10-25 18:03:35, 2026-10-25 20:29:48, 2026-10-26 16:24:05) closed
out as *"orphaned: process gone at daemon boot — cause unrecorded (no deliberate-shutdown
mark)"*, and three runs across two routines concluded the killer was outside what any run could
establish. It was not. The decisive fact is readable from inside the namespace in one call:

    pid 1's START TIME against the orphaned run's own last sign of life.

- pid 1 YOUNGER than the run's last turn ⇒ **the whole container was recreated**: the process
  tree the run lived in no longer exists, which is why no mark was written and why the run could
  not have survived whatever did it. Measured 2026-10-26: pid 1 started 16:23:11, the orphan rows
  are stamped 16:24:05 — 54 s LATER.
- pid 1 OLDER ⇒ the container lived; the DAEMON alone died and was relaunched inside it, which is
  a different investigation (a crash, a supervisor restart, an `exec`).

`restart.read_shutdown_mark` already answers the third case — a deliberate shutdown this daemon
performed — and it is asked FIRST, because a mark is a statement of intent and this is an
inference. What this module adds is the difference between the two causes that previously shared
one word: `unknown`.

## Why "cause unrecorded" was the wrong thing to record

It reads as *nobody can know*, and it is the sentence that sent three runs looking outside the
box. The honest record is narrower and more useful: WHAT happened to the process tree is
knowable; WHO did it (a compose `restart:` policy, a healthcheck, a deploy, an auto-updater) is
outside the namespace and is the operator's to find — filed as R2966, answered by him on
2026-10-26: *"Both: find the restarter AND add the breadcrumb."* This is the breadcrumb half.

## What is deliberately NOT inferred here

Not an OOM. A cgroup at 99% of `memory.max` looks like a smoking gun and is not: on 2026-10-26
`memory.current` was 5.32 of 5.37 GB while `anon` was only 322 MB (the rest `slab_reclaimable` +
`inactive_file`, which are SUPPOSED to fill a cgroup), `memory.pressure` read `full avg10=0.00`,
and `memory.events` showed `oom 0, oom_kill 0` — `max` counts RECLAIM events, not kills. The
reap's own `classify_cause` already answers the OOM question from the dead engine's peak RSS
(F348/F569) and this module never touches it: a plausible number that gets believed is worse
than no number.

And not the HOST's uptime: `/proc/uptime` read 19.9 days through all three kills, so the host
never rebooted. Only the container's own pid 1 answers this question.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("rsched.daemon.containerbirth")

#: Clock ticks per second. `/proc/<pid>/stat`'s `starttime` field is in ticks since boot, and
#: Python has no portable `sysconf(_SC_CLK_TCK)` wrapper that is guaranteed present — but 100 is
#: the value on every Linux this engine runs on, and `os.sysconf` is consulted first where it
#: exists. A wrong tick rate would misdate pid 1 by a factor, so the fallback is the one number
#: that has to be right.
_FALLBACK_HZ = 100

#: Field index (1-based, as `proc(5)` numbers them) of `starttime` in `/proc/<pid>/stat`.
_STARTTIME_FIELD = 22


def _hz() -> int:
    try:
        import os
        return int(os.sysconf("SC_CLK_TCK")) or _FALLBACK_HZ
    except (ValueError, OSError, AttributeError, KeyError):
        return _FALLBACK_HZ


def _boot_epoch(proc: Path) -> float | None:
    """The kernel's boot time as an epoch, from `/proc/stat`'s `btime` line."""
    try:
        for line in (proc / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def pid1_started(proc: Path = Path("/proc")) -> datetime | None:
    """When pid 1 — this container's init — started, or None where /proc cannot answer.

    None is NOT evidence either way: without it the caller keeps the verdict it already had,
    exactly as `classify_cause` treats a missing memory sample.
    """
    boot = _boot_epoch(proc)
    if boot is None:
        return None
    try:
        fields = (proc / "1" / "stat").read_text(encoding="utf-8").rsplit(")", 1)[-1].split()
        # `rsplit(")")` first: pid 1's comm is parenthesized and may itself contain spaces, so
        # splitting the whole line on whitespace mis-indexes every field after it. Field 22 of
        # proc(5) is the 20th token after the comm — the two before it are pid and comm.
        ticks = float(fields[_STARTTIME_FIELD - 3])
    except (OSError, ValueError, IndexError):
        return None
    return datetime.fromtimestamp(boot + ticks / _hz()).astimezone()


def read_breadcrumb(run, cause: str | None, why: str) -> tuple[str, str]:
    """ONE orphan's cause and its sentence, given what the shutdown mark already said.

    The boot reap asks the MARK first and passes its verdict in: a mark is a statement of intent
    by the process that performed the stop, and nothing inferred may override it. This only ever
    refines `unknown` — the honest majority case, and the one that read as *"cause unrecorded"*
    and sent three runs looking outside the box for something readable from inside it.

    Two causes come out of that one word:

    - `container_recreated` — pid 1 is YOUNGER than the run's last sign of life, so the process
      tree the run lived in was replaced. Measured three times in two days.
    - `daemon_died` — pid 1 is OLDER, so the container lived and the daemon alone went: a
      genuinely different investigation, which should not wear the container's verdict.

    Asked PER ORPHAN, not once per boot: each run has its own last sign of life, and one boot
    reaping several may be older than one of them and younger than another. Where `/proc` or the
    status stamp will not answer, the mark's own verdict is returned untouched — an inference
    resting on nothing is worse than an honest `unknown`.
    """
    if cause != "unknown":
        return cause or "unknown", why
    recreated, sentence = recreated_under(last_sign_of_life(run))
    if not sentence:
        return "unknown", why
    return ("container_recreated" if recreated else "daemon_died"), sentence


def last_sign_of_life(run) -> datetime | None:
    """A run's last SIGN OF LIFE — the stamp of its final status write, or None.

    `registry.RunInfo.updated` is written on every turn boundary and on every state change, so
    for a run the daemon finds already dead it is the closest thing to a time of death the box
    keeps. Lenient on purpose: a status file a crash left half-written, or one from a build that
    stamped it differently, must leave the orphan's cause exactly as it was rather than fail the
    boot reap that is trying to close it out.
    """
    raw = str(getattr(run, "updated", "") or "").strip()
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.astimezone()


def recreated_under(last_sign_of_life: datetime | None,
                    proc: Path = Path("/proc")) -> tuple[bool, str]:
    """Was the container recreated UNDER a run whose last sign of life is `last_sign_of_life`?

    Returns `(recreated, sentence)`. The sentence is what a person reads in the run's own
    closing line, so it carries both timestamps: a verdict without its measurement is the kind
    of claim that gets repeated without being checkable.

    A run with no readable last sign of life, or a `/proc` that will not answer, yields
    `(False, "")` — the caller then keeps "cause unrecorded", which is wrong but honest, rather
    than an inference resting on nothing.
    """
    if last_sign_of_life is None:
        return False, ""
    started = pid1_started(proc)
    if started is None:
        return False, ""
    if started <= last_sign_of_life:
        # The container outlived the run: its init is OLDER than the run's last turn, so the
        # daemon alone died inside a living container. Named, because it is the case that is
        # genuinely a daemon crash and should not wear the container's verdict.
        return False, (f"the container was NOT recreated — its init has run since "
                       f"{started.isoformat(timespec='seconds')}, before this run's last sign "
                       f"of life at {last_sign_of_life.isoformat(timespec='seconds')}, so the "
                       f"daemon died inside a living container")
    delta = (started - last_sign_of_life).total_seconds()
    return True, (f"the CONTAINER was recreated under this run: its init (pid 1) started at "
                  f"{started.isoformat(timespec='seconds')}, {delta:.0f}s AFTER this run's last "
                  f"sign of life at {last_sign_of_life.isoformat(timespec='seconds')}. The run "
                  f"could not have survived it and wrote no shutdown mark because nothing in "
                  f"this process tree performed the stop. WHAT recreated the container is "
                  f"outside this namespace — a compose restart policy, a healthcheck, a deploy "
                  f"or an auto-updater — and only the host can say which")
