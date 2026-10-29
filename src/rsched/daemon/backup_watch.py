"""Does the nightly state snapshot still happen? The one reader of backup state.

`rsched-backup.service` failed every night from 2026-09-12 to 2026-10-01 — nineteen nights with no
snapshot — and nobody saw it until a deploy session happened to read the journal. The cause was not
a broken indicator; it was the ABSENCE of one: a grep for `rsched-backup`, `backup-completed` or
`backup_completed` over `src/` and `static/` returned zero hits, so nothing anyone clicks in the
console, and no routine, could tell a healthy instance from an unbacked one (F602).

`deploy/backup.sh` already writes the fact: on a COMPLETE snapshot it stamps
`<root>/.rsched-backup-completed` with an ISO timestamp — the last line of the run, after the
snapshot took its date and `latest` moved. So the check is reading that file and comparing it to the
clock. This module does only that, because the two ends of the problem want different mechanisms and
only one of them is ours:

- a backup that FAILED tonight is the unit's own business, and systemd's `OnFailure=` pushes it to
  the operator's phone — the half that still works when this daemon is down, which is exactly when a
  backup failure matters most (`deploy/rsched-backup-failed.service`);
- a backup that has not COMPLETED for days is a standing state, and it belongs where every other
  "something due did not happen" fact already lives: the health stream, whose `BLOCKED_EVENTS` the
  console renders without being recompiled (`readmodels/health_stream`). A silence of three weeks
  must not be reachable again by either path failing alone.

`staleness` has a SECOND reader since R2428: `rsched postdeploy`'s `backup-freshness` check, which
reports this verdict to an operator standing at a box he has just deployed. It is the same
judgement deliberately — one reader of backup state — read for a different decision, and it
differs in one place: an UNKNOWN (no readable stamp) is a FAIL there and claims nothing here. Both
readings follow from F602. For a health event filed off a 5-second tick, a share this process
cannot see is not evidence that a backup failed; for a person inspecting the instance, the absence
of the indicator IS the finding, because that absence is precisely what hid nineteen nights.

STALE_AFTER_DAYS is 3 rather than 1: the nightly timer has a 15-minute jitter, a host can be off for
a night, and a watchdog that cries after one missed night is one nobody reads by the second week.
Three nights is still three weeks early.

That window is for a night that never STARTED. A night that started and did not finish is evidence,
not jitter, and waiting three days for it is how 2026-10-24 to 10-29 went: every run was killed at
the unit's time limit, `rsched postdeploy` on 10-25 read a 29-hour-old stamp as PASS with two
failed runs already in the journal, and the first `backup_stale` came on day three. So `backup.sh`
also stamps the ATTEMPT (`.rsched-backup-started`) as its first act, and an attempt that began
after the last completion and longer ago than any run may last (ATTEMPT_BOUND_HOURS — the unit's
`TimeoutStartSec`) is stale at once. A killed run writes nothing on its way out, so this inference
is the one record of a failed night that needs no trap to have run.

The event is filed AT MOST ONCE A DAY while the condition holds (`_last_filed`): the daemon ticks
every 5 s, and a row per tick would bury the stream it is meant to make legible — the same rule
`tickguard` applies to a failing item. A stamp that becomes fresh again clears the memory, so the
next staleness is reported rather than swallowed.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import ServerConfig
from ..health_events import log_health_event
from ..readmodels.stamps import instant

log = logging.getLogger("rsched.daemon")

#: How long without a completed snapshot before the instance says so. See the module note.
STALE_AFTER_DAYS = 3

#: The stamp `deploy/backup.sh` writes as its last act on a complete snapshot.
COMPLETED_STAMP = ".rsched-backup-completed"

#: The stamp it writes as its first act, once it has the lock and the share: the attempt.
STARTED_STAMP = ".rsched-backup-started"

#: How long one attempt may run before the unit kills it — `TimeoutStartSec` in
#: deploy/rsched-backup.service, which a test holds this to. An attempt older than this with no
#: completion after it did not complete.
ATTEMPT_BOUND_HOURS = 4

#: The backup root the shipped unit uses. A host that moved it with a drop-in sets `RSCHED_MIRROR`,
#: which `backup_roots` reads first. A root this process cannot see at all (the share is not mounted
#: where the daemon runs) reports UNKNOWN, never stale: claiming a backup failed because we cannot
#: see its share would be the invention F480 removed from the boot reap.
DEFAULT_ROOT = "/mnt/sshd_volume1/rsched-backup"


def backup_roots(env: dict[str, str] | None = None) -> list[Path]:
    """The candidate backup roots, in the order they are tried."""
    environ = env if env is not None else dict(os.environ)
    seen: list[Path] = []
    for raw in (environ.get("RSCHED_MIRROR") or "", DEFAULT_ROOT):
        if raw and Path(raw) not in seen:
            seen.append(Path(raw))
    return seen


def newest_stamp(name: str, roots: list[Path]) -> tuple[datetime | None, Path | None]:
    """The newest instant any root's `name` stamp holds, and which stamp said so — (None, None)
    when no candidate root holds a readable one (an unmounted share, a host that never ran a
    backup, or — for the attempt stamp — a backup.sh older than it).
    """
    newest: datetime | None = None
    which: Path | None = None
    for root in roots:
        stamp = Path(root) / name
        try:
            text = stamp.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        when = instant(text)
        if when is not None and (newest is None or when > newest):
            newest, which = when, stamp
    return newest, which


def staleness(now: datetime | None = None, roots: list[Path] | None = None) -> tuple[str, str]:
    """The instance's backup state as (state, detail) — ONE judgement, so this check and any later
    surface read the same verdict.

    `state` is `ok` (a snapshot completed inside the window and no attempt since has failed),
    `stale` (the last one completed too long ago, OR an attempt after it did not complete) or
    `unknown` (no stamp of either kind can be read — nothing is claimed about the backup).
    """
    candidates = roots if roots is not None else backup_roots()
    when, stamp = newest_stamp(COMPLETED_STAMP, candidates)
    began, _ = newest_stamp(STARTED_STAMP, candidates)
    moment = now or datetime.now(UTC)
    unfinished = (began is not None and (when is None or began > when)
                  and moment - began > timedelta(hours=ATTEMPT_BOUND_HOURS))
    if unfinished and began is not None:
        last = (f"the last COMPLETE one finished {when.isoformat()} ({stamp})" if when is not None
                else "no snapshot has ever completed")
        return "stale", (f"the state snapshot attempt that started {began.isoformat()} did not "
                         f"complete within {ATTEMPT_BOUND_HOURS}h, and {last}. Read it with "
                         "`journalctl --user -u rsched-backup`")
    if when is None:
        looked = ", ".join(str(r) for r in candidates)
        return "unknown", (f"no readable {COMPLETED_STAMP} under any backup root "
                           f"({looked or 'none'}) — the share may not be mounted where this "
                           "process can see it")
    age = moment - when
    if age <= timedelta(days=STALE_AFTER_DAYS):
        return "ok", f"last complete snapshot {when.isoformat()} ({stamp})"
    days = age.days + (1 if age.seconds >= 43200 else 0)
    return "stale", (f"the last COMPLETE state snapshot finished {when.isoformat()}, about "
                     f"{days} days ago ({stamp}) — rsched-backup.service has not completed since. "
                     "Read it with `journalctl --user -u rsched-backup`")


class BackupWatch:
    """The daemon's backup check: one health event per day while the snapshot is stale."""

    def __init__(self, server: ServerConfig) -> None:
        self.server = server
        self._last_filed: datetime | None = None

    async def tick(self) -> None:
        """Read the stamp and file the event if it is due. Never raises into the tick: a check
        about a backup must not be the thing that stops the scheduler.
        """
        try:
            state, detail = staleness()
        except Exception:
            log.exception("backup-watch: reading the completion stamp raised")
            return
        if state != "stale":
            self._last_filed = None     # fresh (or unknown) again: the next staleness is reported
            return
        now = datetime.now(UTC)
        if self._last_filed is not None and now - self._last_filed < timedelta(days=1):
            return
        self._last_filed = now
        log_health_event(self.server.routines_home, "backup_stale",
                         routine="", run_id="", detail=detail)
        log.warning("backup-watch: %s", detail)
