"""F799: a daemon boot reaches the health stream, so a recreate that resumed its runs is countable.

THE DEFECT, measured 2026-10-29: pid 1 restarted at 23:58:22 and run `self-audit:20261029-233250`
continued across it. `health-events.jsonl` held NOTHING — no `orphaned_run` (the run was
resumed, not orphaned), no resume row, no shutdown mark. So the instance's own count of
container recreates misses every CLEAN one, and the only recreates anybody can count are the
ones that left wreckage. That is the wrong bias in a number used to judge whether the daemon is
being killed: it under-reports exactly the cases where the engine coped.

WHAT THIS PINS. One `boot_reaped` event per boot that reaped anything, carrying the four facts
`recover_orphans` and its caller know between them and nothing else does:
  * `pid1_started` — when this process tree's init began, which is what distinguishes a
    container REPLACEMENT from a daemon that died inside a living container;
  * the shutdown mark's reason, or its absence — read before `clear_shutdown_mark` consumes it;
  * `orphans` — how many dead-pid runs were closed out;
  * `resumed` — how many of those a deliberate restart killed and were re-opened (F746).

WHY A BOOT THAT REAPED NOTHING FILES NOTHING. Almost every boot reaps nothing: the daemon
restarts when it is idle, by design. An event per boot would be a row per restart saying "all
quiet", and `health_cooldown` would then suppress the interesting ones behind the quiet ones
within its window. The question this event exists to answer is "was a run alive when the
process tree was replaced, and did it survive?", which only arises when something was reaped.
"""

from __future__ import annotations

import datetime as dt
import json

from rsched.daemon import containerbirth


def _events(home) -> list[dict]:
    path = home / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_boot_that_reaped_files_one_event_with_both_counts(tmp_path):
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)

    containerbirth.log_boot_reap(home, orphans=3, resumed=2,
                                 why="daemon restart (release 0.523.1 at 2026-11-09T02:00:00)")

    rows = [r for r in _events(home) if r.get("event") == "boot_reaped"]
    assert len(rows) == 1, f"expected exactly one boot_reaped row, got {rows}"
    row = rows[0]
    assert row["orphans"] == 3 and row["resumed"] == 2
    assert "3" in row["detail"] and "2" in row["detail"]
    assert "release 0.523.1" in row["detail"], "the mark's own reason must survive into the row"


def test_the_event_carries_pid1s_start_so_a_recreate_is_distinguishable(tmp_path):
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)

    containerbirth.log_boot_reap(home, orphans=1, resumed=1, why="cause unrecorded")

    row = next(r for r in _events(home) if r.get("event") == "boot_reaped")
    # pid1_started reads this container's own /proc, so the test asserts the FIELD is carried
    # and parseable rather than a value — a fixed value here would pin the test host.
    assert "pid1_started" in row, "without pid 1's start a recreate cannot be told from a crash"
    if row["pid1_started"]:
        dt.datetime.fromisoformat(row["pid1_started"])


def test_a_boot_that_reaped_nothing_files_nothing(tmp_path):
    """Every idle restart would otherwise file a row, and the cooldown would then hide the
    boots that did reap something behind the ones that did not."""
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)

    containerbirth.log_boot_reap(home, orphans=0, resumed=0, why="")

    assert [r for r in _events(home) if r.get("event") == "boot_reaped"] == []


def test_the_emitter_never_raises_into_the_boot_path(tmp_path):
    """A diagnostic may not be the reason a daemon fails to start: the boot path's own guard
    logs and continues, but this call sits beside `clear_shutdown_mark` and must not need it.
    An unwritable home is the realistic failure (a full disk at boot is exactly when orphans
    exist) and it must return rather than propagate."""
    missing = tmp_path / "not-a-home"          # no .control, not creatable as a file tree
    (tmp_path / "not-a-home").write_text("this is a FILE where a directory must be")

    containerbirth.log_boot_reap(missing, orphans=2, resumed=0, why="x")   # must not raise


def test_the_scheduler_boot_calls_it_with_both_counts(tmp_path, monkeypatch):
    """The wiring, not just the emitter: the counts live in two different places (the reap
    returns the orphan count, `resume_all` returns the resumed count), so the one caller that
    has both is the boot. A test of the emitter alone would pass with nothing calling it."""
    import inspect

    from rsched.daemon import scheduler

    src = inspect.getsource(scheduler.Scheduler._boot)
    assert "log_boot_reap" in src, "the boot does not emit the event at all"
    assert src.index("log_boot_reap") < src.index("clear_shutdown_mark"), (
        "the mark's reason must be read BEFORE clear_shutdown_mark consumes it")
