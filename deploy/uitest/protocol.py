"""The spool both halves of the BROWSER-STAGE CONTAINER speak (`runner.py` in the `uitest`
service, `submit.py` wherever a caller is) — one copy, so the two cannot drift apart.

WHY THE BROWSER STAGE HAS A CONTAINER OF ITS OWN. Since 0.440.1 the browser suite launches a
Chromium per xdist worker. Run inside the engine container, four workers, four Playwright
drivers and ~25 `chrome-headless-shell` processes shared ONE 5 GiB memory cgroup with the
daemon, and the kernel's memory-cgroup OOM killer took the DAEMON — the process holding every
live run — eight times between 2026-10-22 and 2026-10-30 (`journalctl -k` on the host: each
"Memory cgroup out of memory: Killed process … (rsched)" sits one to two minutes before a burst
of `orphaned_run` rows). Here a browser stage that runs out of memory kills a test worker in its
own cgroup, and the gate reports that; it cannot reach a run.

WHY A SPOOL, NOT A SOCKET. The gate runs inside the engine container, which has no Docker
socket and must not get one. A job is a directory the caller writes and the runner picks up;
the runner writes the output and the exit code back beside it.

WHY THIS GRANTS NOTHING NEW. The spool lives INSIDE the live checkout (`<repo>/.uitest-jobs`),
so whoever can submit a job can already edit the code the daemon imports on its next run — a
strictly larger power than anything the `uitest` container holds (the repo and a read-only
browser cache: no config, no secrets, no routine homes). Nothing here validates a command for
safety, because the boundary is who can write the spool. The env a job carries IS filtered, so
a credential in the caller's environment is never written to disk.

Job layout, `<spool>/<id>/`:
  job.json  {"argv", "cwd", "env"}  — the WHOLE directory appears by one rename, so the runner
            never reads half a job
  log       stdout+stderr, written by the runner as the job runs
  claimed   the runner has started it (its pid inside)
  cancel    the caller wants it ended (SIGTERM to its group, SIGKILL after the grace)
  exit      the exit code, written LAST and atomically — its existence means "finished"
"""
from __future__ import annotations

import os
import time
from pathlib import Path

#: The spool's default home: `.uitest-jobs` in the checkout these files live in. Both the
#: runner (in `uitest`) and the client (in the engine container) mount the checkout at the same
#: path, so the default agrees on both sides. Gitignored.
SPOOL_ENV = "UITEST_SPOOL"
#: Touched by the runner every HEARTBEAT_EVERY_S, idle or busy. A caller reads its age to tell
#: "the runner is working" from "there is no runner", which would otherwise look identical —
#: a job that is simply never picked up.
HEARTBEAT = ".runner-alive"
HEARTBEAT_EVERY_S = 2.0
STALE_AFTER_S = 30.0
#: Exit code for "no runner answered". The SAME number the release gate already reads as
#: `unavailable` (a stage that could not run at all — nothing known about the candidate, never
#: a red), so an absent container is reported in the gate's existing vocabulary.
NO_RUNNER = 77
#: Exit code for "the runner went away while the job ran" (the `uitest` container restarted).
LOST = 125
#: Seconds between SIGTERM and SIGKILL when a job is cancelled or the runner stops.
TERM_GRACE_S = 10.0

#: What a job's environment may carry across. An ALLOW-list, because the caller's environment
#: holds injected secrets (the gate declares BROWSER_CDP_TOKEN) and job.json is a file on disk.
ENV_NAMES = frozenset({"PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
                       "PYTHONFAULTHANDLER", "LANG", "LC_ALL", "TZ"})
ENV_PREFIXES = ("PYTEST_", "AUDIT_GATE_", "RSCHED_TEST_", "RSCHED_SKIP_", "RSCHED_RETRY_",
                "RSCHED_UI_")


def spool() -> Path:
    return Path(os.environ.get(SPOOL_ENV) or Path(__file__).resolve().parents[2] / ".uitest-jobs")


def carried_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k in ENV_NAMES or k.startswith(ENV_PREFIXES)}


def heartbeat_age(root: Path) -> float | None:
    """Seconds since the runner last said it was alive, or None when it never has."""
    try:
        return time.time() - (root / HEARTBEAT).stat().st_mtime
    except OSError:
        return None


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
