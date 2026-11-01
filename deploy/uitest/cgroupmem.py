"""The memory high-water mark of the `uitest` CONTAINER'S OWN memory cgroup, for ONE job.

WHAT THIS MEASURES. The browser stage runs in the `uitest` container (docker-compose.yml says
why), whose memory cgroup is not the engine container's — so the gate process, which lives in
the engine container, cannot read it: nothing has ever verified that a sibling cgroup is even
visible from there, and this file does not assume it is. The reading is taken HERE, inside the
container that does the work, by the runner (runner.py), and travels back to the caller with
the job's result (protocol.py).

WHAT IT CAN AND CANNOT SAY. It can say "this stage peaked at N GB of its M GB ceiling". It can
NEVER say "no OOM occurred". A container that is restarted — exactly what the kernel's
memory-cgroup OOM killer provokes when it takes a container's main process — starts a FRESH
cgroup, so a mark read after a restart knows nothing of what happened before it, and a kill
inside a job leaves no trace in these files at all. Only the host settles that question:
`journalctl -k | grep 'Memory cgroup out of memory'`. Every reading therefore carries
`proves_no_oom: false`, and that field is never anything else.

HOW THE NUMBER IS TAKEN, in order of preference:
  memory.peak    the kernel's own high-water mark — nothing to sample, nothing missed. Writing
                 "0" resets it (Linux 6.1+), so when this job has the container to itself the
                 mark covers exactly this job's duration: `bound: "exact"`. Where the kernel
                 refuses the reset, or another job is running in this same cgroup, the mark
                 includes memory that was not this job's: `bound: "upper"`.
  memory.current sampled while the job runs, for a kernel that exposes no memory.peak. A spike
                 between two samples is missed, so the number is a floor: `bound: "lower"`.

The ceiling comes from `memory.max`, which reads `max` on a container with no limit set (this
repo leaves the `uitest` ceiling to the host's docker-compose.override.yml) — then there is no
percentage to report and `limit_bytes` is absent rather than guessed.

BEST-EFFORT, ALWAYS. A cgroup this process cannot find, read or write changes a browser stage's
outcome by exactly nothing: every read is wrapped and the reading is simply absent, which
protocol.py's reader renders as "not measured" — never as zero. (An earlier instrument in this
repo once killed the pytest session it was only observing; that is the bar this one is held to.)
"""
from __future__ import annotations

import threading
from pathlib import Path

#: Where the cgroup v2 unified hierarchy is mounted, and the file naming this process's place
#: in it. Both are arguments everywhere below, so a test can point them at a tree it built.
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_CGROUP = Path("/proc/self/cgroup")
#: Seconds between `memory.current` samples — used only where the kernel exposes no memory.peak.
SAMPLE_EVERY_S = 0.5


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> int | None:
    """The integer in a cgroup file, or None — which `max` (no ceiling set) reads as too, since
    "no ceiling" is not a number and must not be reported as one.
    """
    try:
        return int(_read_text(path) or "")
    except ValueError:
        return None


def _write_zero(path: Path) -> bool:
    """Reset a kernel high-water mark, where the kernel allows it (memory.peak: Linux 6.1+)."""
    try:
        path.write_text("0", encoding="utf-8")
        return True
    except OSError:
        return False


def _candidates(root: Path, proc: Path) -> list[Path]:
    relative = ""
    for line in (_read_text(proc) or "").splitlines():
        if line.startswith("0::"):                      # the v2 unified hierarchy's own line
            relative = line[3:].strip().lstrip("/")
    return [root / relative, root] if relative else [root]


def cgroup_dir(root: Path | None = None, proc: Path | None = None) -> Path | None:
    """This process's own cgroup v2 directory as the kernel exposes it, or None.

    In a cgroup namespace of its own — the default for a container — `/proc/self/cgroup` reads
    `0::/` and the controller files sit at the mount root; without one it names a sub-path
    (`0::/docker/<id>`) to be read relative to the mount. Both are tried, most specific first,
    and the directory that actually carries the memory files wins. Nothing is hardcoded beyond
    the mount point itself.

    Both paths default to the module's constants, read HERE rather than bound as argument
    defaults, so a test (and a kernel that mounts the hierarchy elsewhere) can rebind them.
    """
    root = CGROUP_ROOT if root is None else root
    proc = PROC_CGROUP if proc is None else proc
    for candidate in _candidates(root, proc):
        if (candidate / "memory.peak").exists() or (candidate / "memory.current").exists():
            return candidate
    return None


def _reading(where: Path, peak: int, bound: str, samples: int, overlapped: bool) -> dict:
    limit = _read_int(where / "memory.max")
    return {"peak_bytes": peak,
            "limit_bytes": limit,
            "peak_pct_of_limit": round(100.0 * peak / limit, 1) if limit else None,
            "bound": bound,
            "samples": samples,
            "cgroup": str(where),
            "jobs_overlapped": overlapped,
            #: Never true, whatever the number is: see this module's docstring.
            "proves_no_oom": False}


class Watch:
    """One job's memory reading: opened when the job starts, read back when it exits.

    `exclusive` says this job has the container's cgroup to itself, which is what makes
    resetting `memory.peak` meaningful. The runner sets `overlapped` on every live Watch when it
    starts a second job, because this cgroup is the CONTAINER's: a concurrent job's memory is
    inside it too, and a reading that cannot tell the two apart says so instead of pretending.
    """

    def __init__(self, where: Path | None, exclusive: bool = True) -> None:
        self.where = where
        self.overlapped = not exclusive
        self._reset = False
        self._samples = 0
        self._sampled: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if where is None:
            return
        if (where / "memory.peak").exists():
            self._reset = exclusive and _write_zero(where / "memory.peak")
        elif (where / "memory.current").exists():
            self._thread = threading.Thread(target=self._sample, name="uitest-memory",
                                            daemon=True)
            self._thread.start()

    def _sample(self) -> None:
        """Poll `memory.current` until the job ends. A daemon thread that only ever reads."""
        while True:
            value = _read_int(self.where / "memory.current") if self.where else None
            if value is not None:
                self._samples += 1
                self._sampled = value if self._sampled is None else max(self._sampled, value)
            if self._stop.wait(SAMPLE_EVERY_S):
                return

    def finish(self) -> dict | None:
        """The job's reading, or None when nothing could be measured. NEVER raises: a failed
        measurement must not change the outcome of the job it was only watching.
        """
        try:
            return self._read_back()
        except Exception:        # EVERY failure, deliberately: see this method's docstring
            return None

    def _read_back(self) -> dict | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=SAMPLE_EVERY_S * 4)
        if self.where is None:
            return None
        if self._sampled is not None:               # sampled: no memory.peak on this kernel
            return _reading(self.where, self._sampled, "lower", self._samples, self.overlapped)
        peak = _read_int(self.where / "memory.peak")
        if peak is None:
            return None
        exact = self._reset and not self.overlapped
        return _reading(self.where, peak, "exact" if exact else "upper", 0, self.overlapped)


def watch(exclusive: bool = True, root: Path | None = None,
          proc: Path | None = None) -> Watch:
    """Start watching this container's own cgroup for the job about to run. Never raises."""
    try:
        return Watch(cgroup_dir(root, proc), exclusive)
    except Exception:            # a job must never fail because its watch could not be opened
        return Watch(None, exclusive)
