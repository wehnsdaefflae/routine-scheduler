"""The daemon runs the post-deploy checklist ITSELF, a short while after boot (F854).

`rsched postdeploy` has existed since 0.445.0 and nothing ever ran it: it is a command a person
types, and `CLAUDE.md` says `drain` and `postdeploy` BRACKET A DEPLOY. But the path this
instance actually takes several times a week is the restart SENTINEL — `build_report` drops
`.control/restart.request` when a release bumps `__version__`, `daemon/restart.py` waits for a
quiet gap and exits for the supervisor — and that path shares no seam with the checklist. So the
identity of what the daemon serves was verified only when someone remembered.

MEASURED, which is why this exists: the daemon restarted 2026-11-08T17:56:26 onto 0.520.0
`28dff48c` and `.control/postdeploy.jsonl`'s newest row was still 2026-11-06T10:00:22 — two days
and at least one restart unverified, with that row's own `backup-freshness` a FAIL nothing
re-checked for sixty hours. A check that runs only when a human types a command is a check whose
SILENCE MEANS NOTHING.

The operator decided the shape (2026-11-09, verbatim): *"the daemon runs the checks itself a
short while after boot (in-process, served-identity hitting its own port once it answers) and
appends the row — no human step ever again"*.

So: once per process, this watcher waits until this instance's own `/api/status` answers, runs
the SAME `cli_postdeploy.run_checks` the command runs, appends the SAME `record_row` to the same
file, and files a health event if anything came back FAIL. Nothing here re-implements a check or
a row: a second implementation of the checklist is two checklists that disagree, and the whole
value of the record is that the automatic rows and the typed ones are comparable.

WHAT THIS SHAPE CANNOT DO, recorded here because a future reader will otherwise assume the
automatic row covers what the typed command covers. `served-identity` exists because a deploy
script once drove the WRONG HOST's Docker daemon (0.374.0) — it answers "which instance
answered, and from where the address came". Asking ourselves over `127.0.0.1` can never catch
that: the right host always answers when it is the one asking. What it does catch is the other
half, and the half the sentinel path breaks: a process that did not relaunch, a `build_skew`
between the served objects and the checkout, a stale backup, a state path the daemon cannot
write. `rsched postdeploy --url <host>` stays the only way to ask "did I deploy the box I think
I did", and `source` says which question the row answers — `"daemon boot"` here, `"--url"` or
`"config bind/port"` from the command.

WHY A DELAY AND NOT A CALL AT STARTUP: the check reads its own HTTP port, so a synchronous call
during startup would check a socket that is not listening yet and record a FAIL about its own
boot. The wait is a poll of the instance's own `/api/status` with a bounded number of attempts;
a port that never answers is a real finding and is recorded as one, by the checklist itself.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import ServerConfig
from ..health_events import log_health_event
from ..ids import now_iso
from ..paths import append_jsonl
from ..postdeploy import FAIL, exit_code, record_row, summary_line

log = logging.getLogger("rsched.postdeploy-watch")

#: How long after boot the first attempt waits. Long enough that uvicorn is serving on an
#: unloaded box, short enough that the record describes the deploy rather than the hour after it.
FIRST_ATTEMPT_S = 20.0

#: Between attempts, and how many. 6 × 15 s ≈ 90 s past the first attempt: a box slow enough to
#: miss that is a box whose port genuinely is not answering, which is the finding — recorded, not
#: retried forever.
RETRY_S = 15.0
ATTEMPTS = 6


async def after_boot(server: ServerConfig) -> None:
    """Run the checklist ONCE, a short while after this process started serving.

    Launched from the app lifespan's startup half (`web/appwiring`) rather than from the
    scheduler's tick, for two reasons: the lifespan IS the moment "this process is now
    serving", which is exactly what the checklist asks about; and once per process is the whole
    cadence — the answer cannot change while the process lives, so a second row from one boot
    would say the same thing with a later stamp and make the record's history unreadable.

    Never raises out: a check ABOUT a deploy must not be the thing that brings the instance
    down, exactly as `backup_watch` and `marker_watch` are written. Cancellation propagates, so
    a shutdown during the wait is clean and leaves no row.
    """
    try:
        await asyncio.sleep(FIRST_ATTEMPT_S)
        await asyncio.to_thread(run_now, server)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("postdeploy-watch: the post-boot checklist raised")


def run_now(server: ServerConfig) -> list | None:
    """The blocking half: poll our own port, run the checks, append the row, file a FAIL.

    Returns the checks (so a test can read the verdicts), or None when the checklist could not
    be run at all. Synchronous by design — it is `urllib` plus a filesystem walk, and it runs in
    a worker thread, never on the loop.
    """
    from ..cli_postdeploy import RECORD_FILE, run_checks

    url = f"http://127.0.0.1:{server.port}"
    checks, status = None, None
    for attempt in range(ATTEMPTS):
        checks, status = run_checks(server, url, "daemon boot")
        if status is not None:
            break
        if attempt + 1 < ATTEMPTS:
            log.info("postdeploy-watch: %s has not answered yet (attempt %d/%d)",
                     url, attempt + 1, ATTEMPTS)
            _sleep(RETRY_S)
    if checks is None:                                  # unreachable in practice
        return None
    log.info("postdeploy-watch: %s", summary_line(checks))
    row = record_row(now_iso(), url, status, checks)
    row["trigger"] = "daemon boot"
    path = server.routines_home / RECORD_FILE
    try:
        append_jsonl(path, row)
    except OSError as exc:      # the checks are the product; the record is best-effort
        log.warning("postdeploy-watch: NOT recorded (%s): %s", path, exc)
    if exit_code(checks):
        _file_failure(server, checks)
    return checks


def _file_failure(server: ServerConfig, checks: list) -> None:
    """A FAIL reaches a surface the operator reads, not only the record.

    The record is a file nothing in the console shows. `postdeploy_failed` is on the
    blocked-work fold, which the Stats panel and the audit both read — so the 2026-11-06
    backup-freshness FAIL that sat unexamined for sixty hours would have been visible that day.
    """
    failed = [c for c in checks if c.verdict == FAIL]
    detail = "; ".join(f"{c.name}: {c.detail}" for c in failed)
    log_health_event(server.routines_home, "postdeploy_failed", routine="", run_id="",
                     detail=f"the post-boot deploy checklist FAILED — {detail}",
                     checks=",".join(c.name for c in failed))
    log.warning("postdeploy-watch: FAIL — %s", detail)


def _sleep(seconds: float) -> None:
    """A blocking sleep, named so the worker-thread wait is obvious at the call site."""
    import time

    time.sleep(seconds)
