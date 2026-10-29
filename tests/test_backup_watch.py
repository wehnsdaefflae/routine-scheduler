"""Nothing in this product read backup state, so three weeks of no backup passed in silence.

`rsched-backup.service` failed every night from 2026-09-12 to 2026-10-01 (F602). A grep for
`rsched-backup` / `backup-completed` over `src/` and `static/` returned zero hits: the indicator was
ABSENT, not broken. Two mechanisms close it, on purpose independent — the unit's own `OnFailure=`
ntfy push, which still works when this daemon is down, and the daemon's `backup_watch`, which reads
the completion stamp and files `backup_stale` into the health stream the console already renders.
Each is tested here for the one property that makes it worth having.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import tmp_server
from rsched.daemon import backup_watch
from rsched.health_events import HEALTH_EVENTS_FILE
from rsched.paths import read_jsonl
from rsched.readmodels.health_stream import BLOCKED_EVENTS

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
NOW = datetime(2026, 10, 2, 4, 0, tzinfo=UTC)


def _stamp(root: Path, when: datetime | str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    text = when if isinstance(when, str) else when.isoformat()
    (root / backup_watch.COMPLETED_STAMP).write_text(f"{text}\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(("age_days", "state"), [
    (0, "ok"),
    (1, "ok"),
    (backup_watch.STALE_AFTER_DAYS, "ok"),          # the window's edge is still ok
    (backup_watch.STALE_AFTER_DAYS + 1, "stale"),
    (19, "stale"),                                  # the real outage, 2026-09-12 to 10-01
])
def test_the_verdict_follows_the_age_of_the_completion_stamp(tmp_path, age_days, state):
    root = _stamp(tmp_path / "share", NOW - timedelta(days=age_days))
    verdict, detail = backup_watch.staleness(now=NOW, roots=[root])
    assert verdict == state, detail
    if state == "stale":
        assert "journalctl --user -u rsched-backup" in detail   # says what to read next


def _began(root: Path, when: datetime) -> None:
    (root / backup_watch.STARTED_STAMP).write_text(f"{when.isoformat()}\n", encoding="utf-8")


@pytest.mark.parametrize(("began_hours_ago", "state"), [
    (None, "ok"),                                         # a backup.sh that stamps no attempt
    (30, "ok"),                                           # began before the completion it led to
    (1, "ok"),                                            # tonight's run is still going
    (backup_watch.ATTEMPT_BOUND_HOURS - 0.5, "ok"),
    (backup_watch.ATTEMPT_BOUND_HOURS + 0.5, "stale"),     # outlived the unit: it was killed
])
def test_an_attempt_that_outlived_the_unit_is_stale_the_morning_after(tmp_path, began_hours_ago,
                                                                      state):
    """2026-10-24 to 10-29: every night was killed at the unit's limit, yet the completion stamp
    stayed inside the three-day window until day three — and `rsched postdeploy` on 10-25 read it
    as PASS with two failed runs already in the journal. A started stamp newer than the completion
    and older than any run may last is a failed night, said the same morning."""
    root = _stamp(tmp_path / "share", NOW - timedelta(hours=29))
    if began_hours_ago is not None:
        _began(root, NOW - timedelta(hours=began_hours_ago))
    verdict, detail = backup_watch.staleness(now=NOW, roots=[root])
    assert verdict == state, detail
    if state == "stale":
        assert "did not complete" in detail and "journalctl --user -u rsched-backup" in detail


def test_a_host_whose_every_attempt_failed_is_stale_not_unknown(tmp_path):
    """No completion stamp used to mean `unknown` — but a readable ATTEMPT stamp proves the share is
    visible, so the absence of a completion beside it is evidence, not an unmounted share."""
    root = tmp_path / "share"
    root.mkdir()
    _began(root, NOW - timedelta(hours=backup_watch.ATTEMPT_BOUND_HOURS + 1))
    verdict, detail = backup_watch.staleness(now=NOW, roots=[root])
    assert verdict == "stale" and "no snapshot has ever completed" in detail


def test_the_attempt_bound_is_the_units_time_limit():
    """The inference is only as true as this equality: a bound shorter than the unit calls a
    running backup failed, a longer one reports a killed night hours late."""
    backup = (DEPLOY / "rsched-backup.service").read_text(encoding="utf-8")
    assert f"TimeoutStartSec={backup_watch.ATTEMPT_BOUND_HOURS}h" in backup.splitlines()
    assert f'> "${{ROOT}}/{backup_watch.STARTED_STAMP}"' in \
        (DEPLOY / "backup.sh").read_text(encoding="utf-8")


def test_an_unreadable_stamp_is_unknown_and_never_claims_a_failure(tmp_path):
    """An unmounted share must not read as a failed backup: the reading the code has is "we cannot
    see it", and asserting a cause it cannot know is the invention F480 removed from the boot reap.
    """
    for roots in ([tmp_path / "nothing-here"], []):
        verdict, detail = backup_watch.staleness(now=NOW, roots=roots)
        assert verdict == "unknown", detail
    garbage = _stamp(tmp_path / "share", "not a timestamp at all")
    verdict, _detail = backup_watch.staleness(now=NOW, roots=[garbage])
    assert verdict == "unknown"


def test_the_newest_stamp_across_roots_wins(tmp_path):
    """A host that moved its root with a drop-in keeps the old one behind: the freshest readable
    stamp is the instance's state, so a stale leftover cannot raise a false alarm."""
    old = _stamp(tmp_path / "old", NOW - timedelta(days=19))
    new = _stamp(tmp_path / "new", NOW - timedelta(hours=2))
    assert backup_watch.staleness(now=NOW, roots=[old, new])[0] == "ok"
    assert backup_watch.staleness(now=NOW, roots=[new, old])[0] == "ok"


def test_rsched_mirror_is_read_before_the_default_root(tmp_path):
    roots = backup_watch.backup_roots({"RSCHED_MIRROR": str(tmp_path / "elsewhere")})
    assert roots[0] == tmp_path / "elsewhere"
    assert Path(backup_watch.DEFAULT_ROOT) in roots
    assert backup_watch.backup_roots({}) == [Path(backup_watch.DEFAULT_ROOT)]


async def test_a_stale_snapshot_is_filed_once_a_day_not_once_a_tick(tmp_path, monkeypatch):
    """The daemon ticks every 5 s. One row per tick would bury the stream this event exists to make
    legible — the rule `tickguard` already applies to a failing item."""
    server = tmp_server(tmp_path)
    root = _stamp(tmp_path / "share", datetime.now(UTC) - timedelta(days=19))
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [root])
    watch = backup_watch.BackupWatch(server)

    for _ in range(5):
        await watch.tick()
    stream = server.routines_home / ".control" / HEALTH_EVENTS_FILE
    rows = [r for r in read_jsonl(stream) if r.get("event") == "backup_stale"]
    assert len(rows) == 1, rows
    assert "days ago" in rows[0]["detail"]

    # fresh again → the memory clears, so the NEXT staleness is reported rather than swallowed
    _stamp(root, datetime.now(UTC))
    await watch.tick()
    _stamp(root, datetime.now(UTC) - timedelta(days=19))
    await watch.tick()
    rows = [r for r in read_jsonl(stream) if r.get("event") == "backup_stale"]
    assert len(rows) == 2, rows


async def test_the_check_never_takes_the_scheduler_down(tmp_path, monkeypatch):
    server = tmp_server(tmp_path)

    def boom(**_kw):
        raise OSError("the share wedged")

    monkeypatch.setattr(backup_watch, "staleness", boom)
    await backup_watch.BackupWatch(server).tick()      # no raise is the assertion


def test_the_event_is_in_the_vocabulary_the_console_renders():
    """An event absent from BLOCKED_EVENTS is read by nobody — exactly how `prompt_oversize_shrunk`
    and `trigger_capped` were written for weeks and surfaced nowhere."""
    assert "backup_stale" in BLOCKED_EVENTS
    assert "snapshot" in BLOCKED_EVENTS["backup_stale"]


def test_the_failure_unit_exists_and_the_backup_unit_starts_it():
    """The half that still works when this daemon is down. It must take its credentials from the
    instance's own secrets store rather than carrying a token, and must not turn a missing topic into
    a second failure."""
    unit = (DEPLOY / "rsched-backup-failed.service").read_text(encoding="utf-8")
    backup = (DEPLOY / "rsched-backup.service").read_text(encoding="utf-8")
    assert "OnFailure=rsched-backup-failed.service" in backup
    assert "EnvironmentFile=-%h/.config/routine-scheduler/secrets.env" in unit
    assert "NTFY_URL" in unit and "NTFY_TOPIC" in unit
    assert "exit 0" in unit            # no topic set → says so, does not fail
    assert "ntfy.sh" not in unit       # the URL comes from the store, never hardcoded
