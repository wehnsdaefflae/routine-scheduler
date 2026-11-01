"""The `uitest` service's whole process: run the jobs callers put in the spool (protocol.py).

Jobs run CONCURRENTLY, each in a process group of its own: serializing the heavy ones is the
gate lock's job (`flock /tmp/rsched-gates.lock`, held by the caller for as long as its job
runs), exactly as it was when the gate ran pytest in the engine container — a browser preflight
must not queue for half an hour behind another gate's browser stage.

A job's environment is the runner's own (HOME, PATH, the image's venv) plus what the caller was
allowed to carry, with the job's own directory first on PYTHONPATH — that is where a caller puts
a plugin that lives outside the checkout (the gate's `gate_progress`), since this container
mounts only the checkout.

Each job also comes back with the PEAK MEMORY of this container's own cgroup for its duration
(cgroupmem.py, carried in `result.json` — protocol.py): this process is the only one that can
read that cgroup, since the caller lives in another container. It says "this stage peaked at
N GB of its M GB ceiling" and it can NEVER say "no OOM occurred" — a restart starts a fresh
cgroup, so only the host's `journalctl -k` settles that. Measuring is best-effort throughout: it
cannot change a job's outcome by one bit.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import cgroupmem
import protocol

#: A finished job's directory is kept this long, for whoever wants to read its log again.
KEEP_FINISHED_S = 24 * 3600
POLL_S = 0.5


class Job:
    def __init__(self, path: Path, proc: subprocess.Popen[bytes], log,
                 watch: cgroupmem.Watch) -> None:
        self.path, self.proc, self.log = path, proc, log
        self.watch = watch
        self.term_sent: float | None = None


def _end(job: Job) -> None:
    """SIGTERM the job's group, then SIGKILL once the grace is spent. Called every poll."""
    now = time.monotonic()
    try:
        if job.term_sent is None:
            job.term_sent = now
            os.killpg(job.proc.pid, signal.SIGTERM)
        elif now - job.term_sent > protocol.TERM_GRACE_S:
            os.killpg(job.proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _finish(job: Job, code: int) -> None:
    """Close the job out: its measurements first, then the `exit` file that means "finished".

    That order is the protocol's (protocol.py): a caller which sees `exit` sees a complete
    result. The measurement is best-effort and `Watch.finish` never raises, so a cgroup that
    could not be read costs the job nothing — its result simply carries no memory reading.
    """
    job.log.close()
    protocol.write_result(job.path, {protocol.MEMORY: job.watch.finish()})
    protocol.write_atomic(job.path / "exit", f"{code}\n")


def start(path: Path, root: Path, exclusive: bool = True) -> Job | None:
    """Start the job in `path`, or finish it at once with exit 2 when it cannot run.

    `exclusive` is False when another job is already running in this container: the memory
    cgroup belongs to the CONTAINER, not to a job, so a reading taken while two jobs share it
    cannot be attributed to either and says so rather than pretending (cgroupmem.py).
    """
    log = (path / "log").open("ab")
    watch: cgroupmem.Watch | None = None
    try:
        spec = json.loads((path / "job.json").read_text(encoding="utf-8"))
        cwd = Path(spec["cwd"]).resolve()
        # Not a security check (protocol.py says why none is needed): a cwd outside the
        # checkout cannot be what the caller meant, since it is the only tree mounted here.
        if not cwd.is_relative_to(root.parent.resolve()) or not cwd.is_dir():
            raise ValueError(f"cwd {cwd} is not a directory inside {root.parent}")
        env = dict(os.environ, **protocol.carried_env(spec.get("env") or {}))
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(path), env.get("PYTHONPATH")) if p)
        # Before the first byte the job allocates, so the high-water mark covers all of it.
        watch = cgroupmem.watch(exclusive=exclusive)
        proc = subprocess.Popen([str(a) for a in spec["argv"]], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.write(f"uitest runner: could not start this job: {exc}\n".encode())
        protocol.write_atomic(path / "claimed", "")
        log.close()
        if watch is not None:
            watch.finish()      # this runner outlives the job: never leave a sampler polling
        protocol.write_atomic(path / "exit", "2\n")
        return None
    protocol.write_atomic(path / "claimed", f"{proc.pid}\n")
    return Job(path, proc, log, watch)


def recover(root: Path) -> None:
    """A job this runner's predecessor started died with it: say so, rather than leave a
    caller waiting on an exit file nobody will write.
    """
    for path in root.iterdir():
        if (path / "claimed").exists() and not (path / "exit").exists():
            with (path / "log").open("a", encoding="utf-8") as log:
                log.write("\nuitest runner: this job was running when the runner stopped "
                          "(the uitest container restarted); its processes are gone.\n")
            protocol.write_atomic(path / "exit", f"{protocol.LOST}\n")


def prune(root: Path) -> None:
    cutoff = time.time() - KEEP_FINISHED_S
    for path in root.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff and (
                    (path / "exit").exists() or not (path / "job.json").exists()):
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def pending(root: Path, running: dict[str, Job]) -> list[Path]:
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name not in running
                  and (p / "job.json").exists() and not (p / "claimed").exists())


def step(root: Path, running: dict[str, Job]) -> None:
    """One pass: start what is new, end what was cancelled, record what exited."""
    for path in pending(root, running):
        # Jobs run concurrently and the memory cgroup is the CONTAINER's: once a second job is
        # in it, neither reading belongs to one job alone, and both say so (cgroupmem.py).
        if job := start(path, root, exclusive=not running):
            for other in running.values():
                other.watch.overlapped = True
            running[path.name] = job
    for name, job in list(running.items()):
        code = job.proc.poll()
        if code is None:
            if (job.path / "cancel").exists():
                _end(job)
            continue
        _finish(job, 128 - code if code < 0 else code)
        del running[name]


def stop(running: dict[str, Job]) -> None:
    """The container is stopping: end every job, then mark each one lost."""
    deadline = time.monotonic() + protocol.TERM_GRACE_S + 2
    while running and time.monotonic() < deadline:
        for name, job in list(running.items()):
            if job.proc.poll() is None:
                _end(job)
            else:
                _finish(job, protocol.LOST)
                del running[name]
        time.sleep(POLL_S)
    for job in running.values():
        _finish(job, protocol.LOST)


def main() -> int:
    root = protocol.spool()
    root.mkdir(parents=True, exist_ok=True)
    recover(root)
    running: dict[str, Job] = {}
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
    print(f"uitest runner: serving {root}", file=sys.stderr, flush=True)
    beat = 0.0
    while not stopping:
        if time.monotonic() - beat >= protocol.HEARTBEAT_EVERY_S:
            (root / protocol.HEARTBEAT).touch()
            prune(root)
            beat = time.monotonic()
        step(root, running)
        time.sleep(POLL_S)
    stop(running)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
