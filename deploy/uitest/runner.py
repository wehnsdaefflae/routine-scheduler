"""The `uitest` service's whole process: run the jobs callers put in the spool (protocol.py).

Jobs run CONCURRENTLY, each in a process group of its own: serializing the heavy ones is the
gate lock's job (`flock /tmp/rsched-gates.lock`, held by the caller for as long as its job
runs), exactly as it was when the gate ran pytest in the engine container — a browser preflight
must not queue for half an hour behind another gate's browser stage.

A job's environment is the runner's own (HOME, PATH, the image's venv) plus what the caller was
allowed to carry, with the job's own directory first on PYTHONPATH — that is where a caller puts
a plugin that lives outside the checkout (the gate's `gate_progress`), since this container
mounts only the checkout.
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

import protocol

#: A finished job's directory is kept this long, for whoever wants to read its log again.
KEEP_FINISHED_S = 24 * 3600
POLL_S = 0.5


class Job:
    def __init__(self, path: Path, proc: subprocess.Popen[bytes], log) -> None:
        self.path, self.proc, self.log = path, proc, log
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
    job.log.close()
    protocol.write_atomic(job.path / "exit", f"{code}\n")


def start(path: Path, root: Path) -> Job | None:
    """Start the job in `path`, or finish it at once with exit 2 when it cannot run."""
    log = (path / "log").open("ab")
    try:
        spec = json.loads((path / "job.json").read_text(encoding="utf-8"))
        cwd = Path(spec["cwd"]).resolve()
        # Not a security check (protocol.py says why none is needed): a cwd outside the
        # checkout cannot be what the caller meant, since it is the only tree mounted here.
        if not cwd.is_relative_to(root.parent.resolve()) or not cwd.is_dir():
            raise ValueError(f"cwd {cwd} is not a directory inside {root.parent}")
        env = dict(os.environ, **protocol.carried_env(spec.get("env") or {}))
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(path), env.get("PYTHONPATH")) if p)
        proc = subprocess.Popen([str(a) for a in spec["argv"]], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.write(f"uitest runner: could not start this job: {exc}\n".encode())
        protocol.write_atomic(path / "claimed", "")
        log.close()
        protocol.write_atomic(path / "exit", "2\n")
        return None
    protocol.write_atomic(path / "claimed", f"{proc.pid}\n")
    return Job(path, proc, log)


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
        if job := start(path, root):
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
