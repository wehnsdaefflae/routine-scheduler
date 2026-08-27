"""What happens AFTER a run's process ends — reaping, recovery, and the queued edits it was holding.

Split out of `runner.py` (F393), the same shape `control.py` uses for helpers lifted out of the
engine loop: the `Runner` owns starting and supervising processes; this owns everything that
happens once one stops.

That is the half where the awkward cases live. A run killed by the OOM killer (rc=-9) is
auto-resumed ONCE from a marker, because a kernel kill is not the run's fault and a silent
retry loop would be worse than either (D99/F348). A run that finished while a user message was
still queued gets resumed rather than leaving the message stranded. Config edits refused while
the run was active are applied here, at the only moment the two-writer race is impossible. And
`recover_orphans` handles the daemon restart: a run whose pid is gone but whose status still
claims it is alive.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .. import registry
from ..config import RoutineConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..paths import atomic_write, atomic_write_json, read_json
from .runner_state import (
    ActiveRun,
    _last_vm_hwm_kb,
    _notable_stderr,
    _pid_alive,
    _stranded_user_messages,
)

log = logging.getLogger("rsched.daemon.runner_reap")


def reap(runner, run: ActiveRun, cfg: RoutineConfig, stderr: bytes) -> None:
    runner.active.pop(run.slug, None)
    if run.cancelled and run.proc is None:
        return   # aborted while queued: status already closed out, nothing ran
    rc = run.proc.returncode if run.proc else None
    info = registry.read_run(run.run_dir, run.slug)
    if info.state in (*registry.ACTIVE_STATES, "unknown"):
        # engine died without closing out (SIGKILL, crash) — the daemon finalizes.
        # F348: the last engine status write sampled vm_hwm_kb (peak resident memory);
        # naming it here turns a blind rc=-9 post-mortem into an OOM diagnosis — a
        # peak near the host's RAM is the kernel-OOM signature.
        hwm = _last_vm_hwm_kb(run.run_dir)
        hwm_note = f"; peak memory VmHWM={hwm} kB" if hwm else ""
        close_out(runner, run.run_dir, run.run_id,
                        f"engine exited rc={rc} without a finish "
                        f"({stderr.decode('utf-8', 'replace')[-400:].strip() or 'no stderr'}"
                        f"{hwm_note})",
                        event="run_canceled" if run.user_cancel else "orphaned_run")
        info = registry.read_run(run.run_dir, run.slug)
        if rc == -9 and not run.user_cancel:
            retry_sigkilled(runner, run, cfg, hwm)
    else:
        # Clean finish: stdout was DEVNULL and stderr is otherwise dropped here, so a
        # non-fatal WARNING/ERROR the engine logged (e.g. a persistent telemetry-write
        # failure like F97) would vanish. Re-emit just those lines into the daemon log
        # (→ docker logs) so a silent, repeating failure is diagnosable.
        notable = _notable_stderr(stderr)
        if notable:
            log.warning("engine-run routine=%s run=%s finished but logged: %s",
                        run.slug, run.run_id, notable)
    if runner.center is not None:
        runner.center.close_process(
            run.run_id,
            error=(info.summary[:200] if info.state in ("failed", "aborted") else None))
    runner.bus.publish({"event": "run_finished", "routine": run.slug, "run_id": run.run_id,
                      "state": info.state, "summary": info.summary[:300]})
    log.info("run_finished routine=%s run=%s rc=%s state=%s",
             run.slug, run.run_id, rc, info.state)
    # R108 residual (F268): a USER message that landed after the engine's LAST inbox
    # check (the web saw the run still live, chose inject-over-resume, and the run
    # finished in between) would strand until a later message nudged it. The reap is
    # the one seam that always runs after every finish, so sweep here: an unconsumed
    # user message re-opens the run through the same terminal-resume a message to an
    # idle conversation takes. Only a CLEAN finish re-wakes — resuming a failed/
    # aborted run on its own leftover message invites a crash-resume loop (and an
    # abort was the user stopping it). Report/trigger/one-shot/audit deliveries never
    # wake: each has its own contract (reports wait for the schedule or the routine's
    # own report trigger).
    if info.state == "finished" and _stranded_user_messages(cfg.dir):
        log.info("post-finish inbox sweep: user message stranded — resuming %s", run.slug)
        resume_for_stranded(runner, cfg)
    # D78-A: a web routine edit made WHILE this run was active was held in the durable
    # pending-edit spool (the git index was contended). The reap is the one seam that
    # always follows a run, and the run is now out of `runner.active`, so no writer
    # contends the index — replay the queued edits in order. Applies after ANY terminal
    # state (config edits are independent of the run's success); a bad edit is logged,
    # not raised, and its file dropped so one can't wedge the queue.
    apply_pending_edits(runner, cfg, run.slug)
    try:
        registry.apply_retention(cfg.dir, cfg.slug, cfg.keep_runs)
    except OSError as exc:
        log.warning("retention failed for %s: %s", cfg.slug, exc)


def retry_sigkilled(runner, run: ActiveRun, cfg: RoutineConfig, hwm: int | None) -> None:
    """D99-A: a run the KERNEL killed (rc=-9, no authored finish, not a user abort)
    gets ONE automatic in-place resume. The run-dir marker caps the retry — a run
    that OOMs again dies failed instead of looping — and the recovery note (filed
    via=background, the one channel a resumed leg's boot drains) makes the resumed
    leg STATE what happened instead of continuing as if nothing did.
    """
    from ..engine.inbox import file_message
    marker = run.run_dir / "sigkill-retry.json"
    if marker.exists():
        log.warning("sigkill retry already spent for %s — run stays failed", run.run_id)
        return
    atomic_write_json(marker, {"ts": now_iso(), "rc": -9, "vm_hwm_kb": hwm})
    hwm_note = f", peak memory {hwm} kB" if hwm else ""
    file_message(cfg.dir,
                 f"AUTOMATIC RECOVERY: this run's previous leg was killed by the kernel "
                 f"(rc=-9, no authored finish{hwm_note} — likely out-of-memory). This is "
                 "the single automatic retry. Reassess from the transcript where the work "
                 "stood, avoid repeating whatever ballooned memory (huge file reads, giant "
                 "observations), and end with an honest authored finish even if that means "
                 "partial.", source="daemon", via="background")

    async def _wake() -> None:
        rid = await runner.resume(cfg, run.run_ts, reason="sigkill-retry")
        if rid:
            log.warning("sigkill auto-resume (D99): %s resumed as %s", run.run_id, rid)
        else:
            log.warning("sigkill auto-resume refused for %s (active/draining/gone) — "
                        "the recovery note stays durable in the inbox", run.run_id)
    task = asyncio.create_task(_wake())
    runner._supervisors.add(task)
    task.add_done_callback(runner._supervisors.discard)


def resume_for_stranded(runner, cfg: RoutineConfig) -> None:
    """Fire-and-forget the terminal resume for a post-finish stranded message (the
    reap itself is sync inside the supervisor's event loop). A refusal — draining,
    raced by another wake — is logged, and the message stays durable in the inbox
    for whatever run comes next.
    """
    async def _wake() -> None:
        rid = await runner.resume_terminal(cfg, reason="converse")
        if not rid:
            log.warning("post-finish inbox sweep could not resume %s — the message "
                        "stays durable for the next run", cfg.slug)
    task = asyncio.create_task(_wake())
    runner._supervisors.add(task)
    task.add_done_callback(runner._supervisors.discard)


def apply_pending_edits(runner, cfg: RoutineConfig, slug: str) -> None:
    """Replay any web edits queued while this run was active (D78-A). Best-effort and
    never raises out of the reap: a spool or applier failure is logged, the run's
    finalization already happened above. No explicit catalog rescan is needed — the
    scheduler tick rescans every registry_rescan_s (scheduler._tick_once), so a queued
    schedule/config change is picked up on the next tick, exactly like a between-run
    web edit.
    """
    from .. import pending_edits
    try:
        rows = pending_edits.apply_pending(cfg.dir, runner.server.routines_home, slug)
    except OSError as exc:
        log.warning("pending-edit replay failed for %s: %s", slug, exc)
        return
    if not rows:
        return
    ok = sum(1 for r in rows if r.get("ok"))
    log.info("pending-edit replay for %s: %d applied, %d failed", slug, ok, len(rows) - ok)
    for r in rows:
        if not r.get("ok"):
            log.warning("pending edit (%s) failed for %s: %s",
                        r.get("kind"), slug, r.get("error"))


def close_out(runner, run_dir: Path, run_id: str, message: str, *,
               event: str = "orphaned_run") -> None:
    """Append a synthetic finish to a dead run (single writer: the engine is gone).
    `event` names the health-stream entry: orphaned_run for a crash/dead pid,
    run_canceled when the death was a user-requested abort (F188) — same payload shape.
    """
    try:
        with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now_iso(), "type": "finish",
                                 "payload": {"status": "failed", "summary": message,
                                             "authored": False}}) + "\n")
    except OSError:
        pass
    raw = read_json(run_dir / "status.json")
    st: dict = raw if isinstance(raw, dict) else {"run_id": run_id}
    st.update(state="failed", updated=now_iso(), question=None)
    atomic_write_json(run_dir / "status.json", st)
    atomic_write(run_dir / "result.md", message + "\n")
    log_health_event(runner.server.routines_home, event,
                     routine=run_id.split(":", maxsplit=1)[0] if ":" in run_id else run_id,
                     run_id=run_id, detail=message[:500])


def recover_orphans(runner, catalog: dict[str, registry.RoutineInfo]) -> int:
    """At boot: any run dir claiming to be alive whose pid is dead gets closed out."""
    fixed = 0
    for info in catalog.values():
        for r in info.runs:
            if r.state in registry.ACTIVE_STATES \
                    and not _pid_alive(r.pid):
                close_out(runner, r.dir, r.run_id, "orphaned by daemon restart")
                fixed += 1
                log.warning("orphan closed: %s", r.run_id)
    return fixed
