"""Run one command in the `uitest` container and behave as if it ran here.

usage: submit.py [--carry FILE]... -- ARGV...

Takes its working directory and environment from its own process, streams the job's output to
its own stdout as it is written, and exits with the job's exit code — so a caller that wrapped
`python -m pytest …` in `flock` keeps every property it relied on: the lock is held for exactly
as long as the job runs, a log file fills as the suite goes, and a SIGTERM to this process (a
deadline, a cancelled gate) ends the job too.

`--carry FILE` copies a file into the job's directory, which the runner puts first on
PYTHONPATH: the way to hand the job a module that lives outside the checkout (the container
mounts only the checkout). PYTHONPATH entries outside the checkout are dropped for the same
reason and named on the first output line.

Exits 77 (protocol.NO_RUNNER) when no runner is answering — start it with
`docker compose up -d uitest` — and 125 (protocol.LOST) when the runner went away mid-job.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import protocol

POLL_S = 0.25
#: How long a cancelled caller waits for the runner to confirm the job ended. Shorter than the
#: 10 s the gate gives a terminated pytest group before its SIGKILL, so the cancel is written and
#: seen before this process can be killed outright.
CANCEL_WAIT_S = 8.0


def _no_runner(root: Path) -> str:
    age = protocol.heartbeat_age(root)
    if age is not None and age <= protocol.STALE_AFTER_S:
        return ""
    seen = "has never answered" if age is None else f"last answered {age:.0f}s ago"
    return (f"uitest runner NOT ANSWERING: the spool {root} {seen}. The browser stage runs in "
            f"the `uitest` container — start it from the checkout: docker compose up -d uitest")


def submit(root: Path, argv: list[str], carry: list[str]) -> tuple[Path, str]:
    """Write the job and return its directory, plus a note naming what was not carried."""
    checkout = root.parent.resolve()
    entries = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    kept = [p for p in entries if Path(p).resolve().is_relative_to(checkout)]
    env = protocol.carried_env(dict(os.environ, PYTHONPATH=os.pathsep.join(kept)))
    name = f"{time.time_ns()}-{os.getpid()}"
    staging = root / f".{name}"
    staging.mkdir(parents=True)
    for path in carry:
        shutil.copy2(path, staging / Path(path).name)
    (staging / "job.json").write_text(json.dumps(
        {"argv": argv, "cwd": str(Path.cwd()), "env": env}), encoding="utf-8")
    staging.rename(root / name)
    dropped = [p for p in entries if p not in kept]
    note = (f"uitest: job {name}"
            + (f"; carried {', '.join(Path(c).name for c in carry)}" if carry else "")
            + (f"; dropped PYTHONPATH outside the checkout: {', '.join(dropped)}"
               if dropped else ""))
    return root / name, note


def follow(job: Path, root: Path) -> int:
    """Stream the job's log to stdout until its exit file appears; return its exit code."""
    out = sys.stdout.buffer
    offset = 0
    while True:
        done = (job / "exit").exists()          # read BEFORE the log, so nothing is lost
        try:
            with (job / "log").open("rb") as log:
                log.seek(offset)
                chunk = log.read()
        except FileNotFoundError:
            chunk = b""
        if chunk:
            out.write(chunk)
            out.flush()
            offset += len(chunk)
        if done:
            return int((job / "exit").read_text(encoding="utf-8").strip() or protocol.LOST)
        if problem := _no_runner(root):
            out.write(f"\n{problem}\n".encode())
            out.flush()
            if (job / "claimed").exists():
                return protocol.LOST
            # Never picked up: withdraw it, or a runner started later would run a job whose
            # caller has already gone.
            shutil.rmtree(job, ignore_errors=True)
            return protocol.NO_RUNNER
        time.sleep(POLL_S)


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="submit.py")
    parser.add_argument("--carry", action="append", default=[])
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    opts = parser.parse_args(args)
    argv = opts.argv[1:] if opts.argv[:1] == ["--"] else opts.argv
    if not argv:
        parser.error("nothing to run: pass the command after --")
    root = protocol.spool()
    if problem := _no_runner(root):
        print(problem, flush=True)
        return protocol.NO_RUNNER
    job, note = submit(root, argv, opts.carry)
    print(note, flush=True)

    def cancel(signum, _frame):
        (job / "cancel").touch()
        ends = time.monotonic() + CANCEL_WAIT_S
        while not (job / "exit").exists() and time.monotonic() < ends:
            time.sleep(POLL_S)
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, cancel)
    return follow(job, root)


if __name__ == "__main__":
    raise SystemExit(main())
