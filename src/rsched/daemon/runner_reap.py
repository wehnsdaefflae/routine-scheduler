"""What happens AFTER a run's process ends — reaping, recovery, and the queued edits it was holding.

Split out of `runner.py` (F393), the same shape `control.py` uses for helpers lifted out of the
engine loop: the `Runner` owns starting and supervising processes; this owns everything that
happens once one stops.

That is the half where the awkward cases live. A run killed by the OOM killer (rc=-9) is
auto-resumed ONCE from a marker, because a kernel kill is not the run's fault and a silent
retry loop would be worse than either (D99/F348). A run that finished while a user message was
still queued gets resumed rather than leaving the message stranded. Config edits refused while
the run was active are applied here, at the only moment the two-writer race is impossible. And
`recover_orphans` handles the boot case: a run whose pid is gone but whose status still claims
it is alive. It used to call that "the daemon restart" and write the same sentence into every
such run — an assumption the code was in no position to make, since a container stop, a crash
and an OOM arrive identically. It now reads the cause instead (F480).
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
                        event="run_canceled" if run.user_cancel else "orphaned_run",
                        rc=rc, vm_hwm_kb=hwm,
                        cause=classify_cause(rc, user_cancel=run.user_cancel,
                                             vm_hwm_kb=hwm))
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
    if (info.state == "finished" and info.outcome != "skipped"
            and _stranded_user_messages(cfg.dir)):
        log.info("post-finish inbox sweep: user message stranded — resuming %s", run.slug)
        resume_for_stranded(runner, cfg)
    # D78-A: a web routine edit made WHILE this run was active was held in the durable
    # pending-edit spool (the git index was contended). The reap is the one seam that
    # always follows a run, and the run is now out of `runner.active`, so no writer
    # contends the index — replay the queued edits in order. Applies after ANY terminal
    # state (config edits are independent of the run's success); a bad edit is logged,
    # not raised, and its file dropped so one can't wedge the queue.
    apply_pending_edits(runner, cfg, run.slug)
    prune_runs(runner, cfg)


def prune_runs(runner, cfg: RoutineConfig) -> None:
    """Apply the routine's run retention OFF the event loop.

    The reap runs inside the supervisor task, on the loop thread, and retention re-indexes
    every run dir, `rmtree`s the oldest and gzips transcripts that can reach 9 MB — so every
    SSE stream, API request and scheduler tick waited on it. It never touches a live run (the
    newest dirs are kept), so there is nothing to serialize it against: hand it to a thread
    and let the reap return.
    """
    async def _prune() -> None:
        try:
            await asyncio.to_thread(registry.apply_retention, cfg.dir, cfg.slug, cfg.keep_runs)
        except OSError as exc:
            log.warning("retention failed for %s: %s", cfg.slug, exc)

    runner.spawn(_prune())


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
    # F569: the leg that reads this note treats it as established fact, so it must not name a
    # cause the evidence contradicts. When the peak is far below the host's RAM the kill did not
    # come from memory pressure, and telling the resumed leg to "avoid whatever ballooned memory"
    # sends it hunting a cause that never existed.
    memory_ruled_out = classify_cause(-9, vm_hwm_kb=hwm) != "oom_kill"
    if memory_ruled_out:
        diagnosis = (f" Peak memory was only {hwm} kB, far below this host's RAM, so this was "
                     "NOT an out-of-memory kill — something else sent the signal (a supervisor "
                     "stop, a deploy, a manual kill). Do not spend this leg avoiding memory use")
    else:
        diagnosis = (f"{hwm_note} — likely out-of-memory. Avoid repeating whatever ballooned "
                     "memory (huge file reads, giant observations)")
    file_message(cfg.dir,
                 f"AUTOMATIC RECOVERY: this run's previous leg was killed (rc=-9, no authored "
                 f"finish).{diagnosis}. This is the single automatic retry. Reassess from the "
                 "transcript where the work stood, and end with an honest authored finish even "
                 "if that means partial.", source="daemon", via="background")

    async def _wake() -> None:
        rid = await runner.resume(cfg, run.run_ts, reason="sigkill-retry")
        if rid:
            log.warning("sigkill auto-resume (D99): %s resumed as %s", run.run_id, rid)
        else:
            log.warning("sigkill auto-resume refused for %s (active/draining/gone) — "
                        "the recovery note stays durable in the inbox", run.run_id)
    runner.spawn(_wake())


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
    runner.spawn(_wake())


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


#: A SIGKILL whose peak resident memory is BELOW this fraction of the host's RAM did not run out
#: of memory, whatever rc=-9 suggests on its own. F348 sampled the peak precisely because "a peak
#: near the host's RAM is the kernel-OOM signature" — but nothing ever compared the two, so every
#: SIGKILL wore the OOM verdict. Measured 2026-09-26 (F569): a conversation reaped twice at
#: VmHWM 61,716 kB and 64,492 kB — ~60 MB — was called `oom_kill` and its resumed leg was told
#: "likely out-of-memory" as established fact. A tenth is deliberately generous: the kernel kills
#: the biggest consumer under pressure, so a true victim sits near the ceiling, not a rounding
#: error below it.
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

    The live reap reads `rc` and then threw the distinction away: every death became
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


def close_out(runner, run_dir: Path, run_id: str, message: str, *,
               event: str = "orphaned_run", rc: int | None = None,
               vm_hwm_kb: int | None = None, status: str = "failed",
               cause: str | None = None) -> None:
    """Append a synthetic finish to a dead run (single writer: the engine is gone).
    `event` names the health-stream entry: orphaned_run for a crash/dead pid,
    run_canceled when the death was a user-requested abort (F188) — same payload shape.

    `rc` and `vm_hwm_kb` ride along as STRUCTURED health fields when the reap knows them
    (F422): the two events differ by who asked for the death, not by how the process died,
    so "was this a signal kill?" is only answerable from the exit status. An orphan
    recovered at boot has no process left to report on and passes neither.

    `cause` names WHY the process is gone and is the field F480 exists for. Left unset it is
    derived from `rc` by `classify_cause`, which is right for every live-reap caller; the boot
    reap passes it explicitly because it has no rc and must not let a derived default speak
    for evidence it does not have.

    `status` is the TERMINAL STATE to record, and it is not always `failed` (R1512/R1514).
    A run the daemon itself killed by restarting did not fail — nothing about the routine or
    its recipe went wrong — but it was written as `failed` at turn 0, so on 2026-09-14 five
    routines showed a failure they did not cause and a sweep had to open each result.md to
    tell an infrastructure event from a broken recipe. `aborted` already means "ended because
    something outside the run said stop" everywhere else (registry.TERMINAL_STATES, the health
    read model, the CLI exit map), so the honest close-out reuses it rather than inventing a
    sixth state.
    """
    try:
        with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now_iso(), "type": "finish",
                                 "payload": {"status": status, "summary": message,
                                             "authored": False}}) + "\n")
    except OSError:
        pass
    raw = read_json(run_dir / "status.json")
    st: dict = raw if isinstance(raw, dict) else {"run_id": run_id}
    st.update(state=status, updated=now_iso(), question=None)
    atomic_write_json(run_dir / "status.json", st)
    atomic_write(run_dir / "result.md", message + "\n")
    log_health_event(runner.server.routines_home, event,
                     routine=run_id.split(":", maxsplit=1)[0] if ":" in run_id else run_id,
                     run_id=run_id, detail=message[:500], rc=rc, vm_hwm_kb=vm_hwm_kb,
                     cause=cause if cause is not None else classify_cause(rc))


def recover_orphans(runner, catalog: dict[str, registry.RoutineInfo]) -> int:
    """At boot: any run dir claiming to be alive whose pid is dead gets closed out.

    The boot reap knows ONE fact — the pid is gone — and until F480 it reported a second one
    it had never established: every orphan was written as "orphaned by daemon restart". A
    container stop, a crash, an OOM during a long gate and a deploy replacing code under a
    running process all wear that sentence, and three separate investigations (F480, R1501,
    R1515) went looking for a broken drain because of it. The drain was never broken.

    So the cause is now READ rather than assumed, from the breadcrumb a deliberate shutdown
    leaves (`restart.read_shutdown_mark`), and it rides out as a structured `cause` field:
    `daemon_restart` when the mark says this exit was asked for, `unknown` when nothing
    established why the process is gone. `unknown` is the honest majority case and it is
    meant to be visible — an audit filtering for it is asking "what killed these runs?",
    which is a question the stream could not previously be asked at all.
    """
    from . import restart
    # Read the mark LAZILY, at the first orphan. The boot path runs on every daemon start,
    # almost always with nothing to reap, and reading it eagerly would demand `runner.server`
    # from every caller — including the scheduler's runner double, which has no such attribute
    # and whose boot loop died on it (4 scheduler tests, this run).
    #
    # Lazy READING is not lazy EXPIRY, and conflating the two was a defect: a boot that
    # orphaned nothing never consumed the mark, so it sat there for the next crash to inherit.
    # `Scheduler.run_forever` expires it once, after all three reap passes have had their
    # chance to read it (restart.clear_shutdown_mark).
    cause: str | None = None
    why = ""

    def _resolve() -> None:
        nonlocal cause, why
        if cause is not None:
            return
        mark = restart.read_shutdown_mark(runner.server.routines_home)
        if mark is None:
            cause, why = "unknown", "cause unrecorded (no deliberate-shutdown mark)"
        else:
            cause = "daemon_restart"
            why = f"daemon restart ({mark.get('reason') or 'requested'} at {mark.get('ts')})"

    fixed = 0
    for info in catalog.values():
        for r in info.runs:
            if r.state in registry.ACTIVE_STATES \
                    and not _pid_alive(r.pid):
                _resolve()
                # ABORTED, not failed (R1512/R1514): the daemon stopped this run by
                # restarting, so a `failed` here is a failure the routine never had.
                close_out(runner, r.dir, r.run_id,
                          f"orphaned: process gone at daemon boot — {why}",
                          status="aborted", cause=cause)
                fixed += 1
                log.warning("orphan closed: %s (cause=%s)", r.run_id, cause)
    return fixed
