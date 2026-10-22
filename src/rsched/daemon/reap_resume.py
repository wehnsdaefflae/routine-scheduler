"""The reap's AUTOMATIC resumes — a run the daemon re-opens without anyone asking.

Split out of `runner_reap.py` (which owns everything that happens once a process stops) because
F746 added a third one and the file was at its size ceiling. They belong together anyway: each
answers the same question — *this run did not end because its work was done; does the daemon
start it again?* — and each obeys the same three rules, learned one incident at a time:

  * a DURABLE record first, so a refused resume loses nothing (the recovery note sits in the
    inbox and the next scheduled run reads it);
  * a CAP, so a condition that repeats cannot become a resume loop;
  * a NOTE the resumed leg reads, because a leg that continues as if nothing happened spends
    its turns rediscovering that something did.

A global pause holds every one of them (`pause.holds_automatic_leg`): a paused instance means
the operator wants nothing starting, and an automatic leg is the easiest kind to forget.
"""

from __future__ import annotations

import logging

from .. import registry
from ..config import RoutineConfig
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

log = logging.getLogger("rsched.daemon.reap_resume")

#: How many times a single run may be re-opened after a daemon restart killed it. The restart is
#: never the run's fault, so one is too few: on 2026-10-22 two boots 49 minutes apart orphaned the
#: SAME two runs, and a cap of one would have left the second pair dead (R2800 measured 79 orphans
#: over 50 boots). But a daemon that boot-LOOPS would otherwise re-open every run on every boot
#: forever, so the count is kept in the run dir and read back — three covers a deploy sequence and
#: stops a loop.
RESTART_RESUMES = 3


def resume_for_stranded(runner, cfg: RoutineConfig) -> None:
    """Resume for a stranded post-finish message; every refusal leaves it durable in the inbox."""
    from . import pause
    if pause.holds_automatic_leg(runner.server, "post-finish inbox wake", cfg.slug):
        return
    async def _wake() -> None:
        rid = await runner.resume_terminal(cfg, reason="converse")
        if not rid:
            log.warning("post-finish inbox sweep could not resume %s — the message "
                        "stays durable for the next run", cfg.slug)
    runner.spawn(_wake())


#: Orphan states the boot resume takes. A run that was WORKING had its turn interrupted and can
#: carry on; one parked on a human (waiting_user/paused) must not be re-opened automatically —
#: its question is durable on the Decisions page, and re-entering the wait would re-ask the
#: operator something he is already looking at.
RESUMABLE_ORPHAN_STATES = ("queued", "starting", "running")


def restart_resume_blocker(run: registry.RunInfo) -> str | None:
    """Why this orphan is NOT re-opened after a daemon restart, or None when it is.

    Separated from the act for the reason `runner.resume_blocker` is: the log line and the
    decision then cannot disagree, and a run left closed always has a stated reason.
    """
    if run.state not in RESUMABLE_ORPHAN_STATES:
        return f"it was parked ({run.state}), so its question stays durable instead"
    spent = _resumes_spent(run)
    if spent >= RESTART_RESUMES:
        return (f"the daemon has already re-opened it {spent} times "
                f"(cap {RESTART_RESUMES}) — something is restarting repeatedly")
    return None


def _marker(run: registry.RunInfo):
    return run.dir / "restart-resume.json"


def _resumes_spent(run: registry.RunInfo) -> int:
    mark = read_json(_marker(run))
    return int(mark.get("resumes", 0)) if isinstance(mark, dict) else 0


def resume_all(runner, rows: list) -> int:
    """Re-open every restart-killed orphan the boot reap collected; returns how many were asked.

    One place so the boot stays one line and the policy stays here. Each row is driven
    independently — a routine whose dir refuses a write must not strand the ones after it, the
    same rule the close-out loop follows.
    """
    woken = 0
    for cfg, run, why in rows:
        try:
            woken += bool(resume_after_restart(runner, cfg, run, why))
        except Exception:
            log.exception("restart orphan resume of %s failed", getattr(run, "run_id", "?"))
    if woken:
        log.warning("F746: %d of %d restart-killed runs re-opened at boot", woken, len(rows))
    return woken


def resume_after_restart(runner, cfg: RoutineConfig, run: registry.RunInfo, why: str) -> bool:
    """F746: re-open a run that a DELIBERATE daemon restart killed mid-work.

    The operator's ruling, 2026-10-22: *"Drain in-flight runs before any restart, and resume
    rather than abort"*. The drain half is the restart sentinel's (the daemon waits for a quiet
    gap before exiting on its own request); this is the other half, for the exit no drain can
    hold — an EXTERNAL SIGTERM from a deploy, a `systemctl restart`, a container replacement.
    uvicorn bounds its graceful shutdown to 10 s (`cli_daemon`) and a run lasts hours, so the
    exit cannot wait for the work: the honest equivalent is that the work CONTINUES on the next
    boot instead of dying at the turn it reached.

    What it costs to not do this, measured: `self-audit:20261021-223212` was closed `aborted` at
    turn 240 — 5125 s of work — by the 07:39 boot on 2026-10-22, and
    `llmsectest-weekday:20261022-040003` lost a sequential child twice in 49 minutes. Across the
    health stream, 79 runs over 50 boots ended this way (R2793/R2800).

    Only called for an orphan whose cause the shutdown mark established as `daemon_restart`: a
    crash, an OOM or an unexplained death must NOT be re-opened here — the OOM path has its own
    single retry (`runner_reap.retry_sigkilled`) and an unknown death is exactly the case where
    the daemon knows too little to start anything again.

    Returns True when a resume was requested. The close-out has already happened, so a False
    leaves the ordinary `aborted` record and loses nothing.
    """
    from ..engine.inbox import file_message
    from . import pause
    if pause.holds_automatic_leg(runner.server, "restart orphan resume", cfg.slug):
        return False
    if blocker := restart_resume_blocker(run):
        log.warning("restart orphan %s not resumed: %s", run.run_id, blocker)
        return False
    spent = _resumes_spent(run) + 1
    atomic_write_json(_marker(run), {"ts": now_iso(), "resumes": spent, "why": why})
    file_message(cfg.dir,
                 "AUTOMATIC RECOVERY: this run was interrupted mid-turn because the daemon went "
                 f"down ({why}) — not by anything this run did, and not by a budget. It is now "
                 f"resumed in place (automatic resume {spent} of {RESTART_RESUMES}). Re-read the "
                 "transcript for where the work actually stood: the last turn may have been cut "
                 "off before its observation, and any CHILD RUN that was in flight did not "
                 "survive, so re-issue what you still need. End with an honest authored finish.",
                 source="daemon", via="background")

    async def _wake() -> None:
        rid = await runner.resume(cfg, run.ts, reason="restart-resume")
        if rid:
            log.warning("restart orphan resumed (F746): %s re-opened as %s", run.run_id, rid)
        else:
            log.warning("restart orphan %s could not be resumed (active/draining/gone) — the "
                        "recovery note stays durable in its inbox", run.run_id)
    runner.spawn(_wake())
    return True
