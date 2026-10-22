"""CLI commands that act on LIVE RUNS: abort one, or drain them all before a deploy.

`cmd_abort` moved here from `cli.py` (which was at its size ceiling) because it belongs beside
the drain: both reach into the runs that are in flight right now, both resolve a slug across all
three homes, and `cli.py` keeps what builds the parser.

A command's hermetic test depends on `load_server_config` being patchable ON ITS OWN MODULE —
the suite's `cli_server` fixture patches the seam in `cli` AND here, and a new cmd_* module
without that patch loads the PRODUCTION config and answers about the real instance. So import
the loader at module level (as below) rather than inside the function.

## `rsched drain` — quiesce the instance BEFORE a deploy restarts it (F746)

The operator's ruling, 2026-10-22: *"Drain in-flight runs before any restart, and resume rather
than abort"*. The resume half lives in `daemon/reap_resume.py`, for the exit that cannot be
helped. This is the half that prevents the loss in the first place.

WHY A COMMAND AND NOT A SIGNAL HANDLER. uvicorn bounds its graceful shutdown to 10 s
(`cli_daemon`) and systemd/compose give the process 20 s; a routine run lasts minutes to hours.
So a drain can never happen INSIDE the exit — by the time SIGTERM arrives the deploy has already
decided. It has to happen before, which means something the deployer runs:

    rsched drain && docker compose up -d --build

Measured 2026-10-22 (R2792/R2793/R2800): two boots 49 minutes apart orphaned the same two runs;
`self-audit:20261021-223212` lost 240 turns and 5125 s of work; a sequential child of
`llmsectest-weekday` was destroyed twice. `deploy/DOCKER.md` already said to "check `active_runs`
on /api/status first, or rebuild in a quiet hour" — a human instruction nothing enforced, and
79 orphaned runs over 50 boots is what that costs.

WHAT THE DRAIN DOES, and nothing more: it drops the PAUSE sentinel (`daemon/pause.py` — the
scheduler then fires nothing on its own, exactly as the Settings pause does), then waits for the
runs already in flight to finish on their own. It never kills a run and never restarts anything:
the deploy command the operator typed does that, after this exits 0. On a timeout it exits 1 with
what is still running, so `&&` does not proceed and the choice stays the deployer's.

The pause is LEFT IN PLACE on success, because the deploy is still to come: the new process reads
the same sentinel and keeps firing nothing until `rsched drain --resume` (or the Settings toggle)
lifts it. A drain that resumed scheduling on its way out would hand the next 30 seconds of fires
to the process that is about to be replaced.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from . import registry
from .config import ServerConfig, load_server_config
from .daemon import pause


def _dir_across_homes(server, slug: str) -> Path:
    """Resolve a slug across routines, conversations, and background homes — `rsched
    abort` applies to any of them (conversations and detached tasks are runs too).
    """
    for home in registry.all_homes(server):
        if (home / slug / "routine.yaml").is_file():
            return home / slug
    return server.routines_home / slug   # let downstream produce the not-found error


def cmd_abort(args) -> int:
    """Abort `<slug>` (its newest active run) or `<slug>:<ts>` (that run).

    Only an ACTIVE run is signalled, in both forms. A finished run's status.json keeps the
    pid it ran under, and the OS hands pids out again, so signalling a named finished run
    reached whatever process group holds that pid NOW.
    """
    import asyncio

    from .daemon.runner_state import abort_process
    from .ids import parse_run_id

    server, _ = load_server_config()
    target = args.run_id
    ts = ""
    if ":" in target:
        try:
            slug, ts = parse_run_id(target)
        except ValueError as exc:
            print(f"error: {exc} — expected <slug> or <slug>:<YYYYMMDD-HHMMSS>",
                  file=sys.stderr)
            return 2
    else:
        slug = target
    runs = [r for r in registry.run_index(_dir_across_homes(server, slug), slug)
            if not ts or r.ts == ts]
    alive = [r for r in runs if r.state in registry.ACTIVE_STATES]
    if not alive:
        print(f"run {target} is not active ({runs[0].state})" if ts and runs
              else f"no active run for {target}", file=sys.stderr)
        return 1
    run = alive[0]
    ok = asyncio.run(abort_process(run.pid))
    print(f"abort {'sent' if ok else 'failed — process not found'} for {run.run_id}",
          file=sys.stderr)
    return 0 if ok else 1


def active_runs(server: ServerConfig) -> list[str]:
    """Every run claiming to be alive across all three homes, read off disk.

    The registry is the interface (no process is asked), which is why this works from a deploy
    shell with no access to the daemon's memory — and why a dead pid still claiming `running`
    shows up here. That is deliberate: a drain must not report quiet while an orphan row is
    open, or the boot reap's attribution lands on a run the deployer was told about.
    """
    return sorted(run.run_id
                  for home in registry.all_homes(server)
                  for info in registry.scan(server, home).values()
                  for run in info.runs
                  if run.state in registry.ACTIVE_STATES)


def cmd_drain(args) -> int:
    """Pause scheduling and wait for in-flight runs; 0 when quiet, 1 on timeout."""
    server, _ = load_server_config()
    if args.resume:
        pause.set_paused(server, False)
        print("scheduling resumed", file=sys.stderr)
        return 0
    pause.set_paused(server, True)
    deadline = time.monotonic() + args.timeout
    while True:
        active = active_runs(server)
        if not active:
            print("drained: nothing is running — scheduling stays paused until "
                  "`rsched drain --resume`", file=sys.stderr)
            return 0
        if time.monotonic() >= deadline:
            print(f"NOT drained after {args.timeout}s — still running: {', '.join(active)}",
                  file=sys.stderr)
            print("restarting now would interrupt these runs (they are re-opened at the next "
                  "boot, but their in-flight children are lost)", file=sys.stderr)
            return 1
        print(f"waiting for {len(active)} run(s): {', '.join(active)}", file=sys.stderr)
        time.sleep(args.poll)
