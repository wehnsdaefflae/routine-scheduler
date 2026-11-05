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

After the job's output, one line reports what the runner MEASURED about it:
`uitest memory: {"peak_bytes": …, "limit_bytes": …, "peak_pct_of_limit": …, "bound": …}` — the
peak of the `uitest` CONTAINER's own memory cgroup for the job's duration, sampled inside that
container because this process cannot read its cgroup from here. It answers "this stage peaked
at N GB of its M GB ceiling" and can NEVER answer "no OOM occurred": a restart starts a fresh
cgroup, so only the host's `journalctl -k` settles that. No line at all means not measured.
`bound` says which number it is: `exact` when the kernel's own mark was reset for this job, and
`lower` when it was sampled because that reset was refused — which is what this container does,
since it mounts the cgroup read-only. A sampled reading also carries the unreset kernel mark as
`cgroup_lifetime_peak_bytes`: the container's whole life, an upper bound beside the lower one.

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


def _report_memory(job: Path, out) -> None:
    """Print what the runner measured about this job's memory, when it measured anything.

    The number is the peak of the `uitest` CONTAINER's own memory cgroup for the job's duration,
    taken inside that container (cgroupmem.py) because this process cannot read it from here. It
    says "this stage peaked at N GB of its M GB ceiling"; it can NEVER say "no OOM occurred",
    since a restart starts a fresh cgroup and only the host's `journalctl -k` settles that.

    An absent reading means NOT MEASURED — an older runner, or a cgroup it could not read — and
    prints nothing at all rather than a zero.
    """
    memory = protocol.job_result(job).get(protocol.MEMORY)
    if memory is None:
        return
    try:
        out.write(f"{protocol.MEMORY_LINE}{json.dumps(memory)}\n".encode())
        out.flush()
    except (OSError, TypeError, ValueError):
        pass


def follow(job: Path, root: Path) -> int:
    """Stream the job's log to stdout until its exit file appears; return its exit code.

    A finished job's measurements (`_report_memory`) follow its output, on one line of their
    own: `exit` is written last, so by then `result.json` is complete.
    """
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
            _report_memory(job, out)
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
