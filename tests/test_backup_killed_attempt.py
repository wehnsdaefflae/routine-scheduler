"""D299/F931: the backup outcome that HAPPENS is the one that must be recorded. 0.603.0 gave
`deploy/backup.sh` three record sites — the local tier completing, the off-box tier completing,
the off-box tier failing with a non-zero rsync — and all three are reached only by a script that
runs to its own end, while on this host the off-box tier's usual ending is a SIGTERM at the
unit's `TimeoutStartSec` that kills bash inside rsync before any of them (measured 2026-11-24
from the unit itself: `Result=timeout`, `ExecMainStatus=15`, started 01:13:20, killed 07:13:20,
beside a `backup-attempts.jsonl` holding exactly one row for that night, `local complete`) — so
the record D299 exists for was blind to the failure D299 was filed about, and a reader could not
tell "killed at its bound" from "never ran at all". These tests signal the process GROUP because
that is the measured path: systemd's default `KillMode=control-group` kills the rsync and lets
bash's wait return so the trap runs, while a signal delivered to bash ALONE leaves the trap
pending behind the foreground child and records nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from helpers import write_executable
from rsched.daemon.backup_attempts import ATTEMPTS_FILE, attempt_detail
from test_backup_snapshots import REAL_DATE, REAL_RSYNC, SHM
from test_deploy_state import DEPLOY, _env, _home

BACKUP_SH = DEPLOY / "backup.sh"

#: An rsync stub that hands the LOCAL tier's call to the real rsync and HANGS on the off-box
#: one, so the night is killed where this host's nights are killed. `--delete-excluded` is the
#: off-box call's own flag (the local tier never passes it).
HANGING_OFFBOX_RSYNC = f"""#!/bin/sh
for arg in "$@"; do
  if [ "$arg" = "--delete-excluded" ]; then
    echo "offbox rsync hanging, as the NAS does" >&2
    sleep 300
    exit 0
  fi
done
exec {REAL_RSYNC} "$@"
"""


@pytest.fixture
def home(tmp_path) -> Path:
    return _home(tmp_path)


def _roots(tmp_path: Path):
    """A local tier and an off-box one, on a filesystem apart from the HOME's."""
    if REAL_RSYNC is None:
        pytest.skip("backup.sh copies with rsync, which this host does not have")
    if not SHM.is_dir() or not os.access(SHM, os.W_OK) \
            or SHM.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("needs a writable filesystem apart from tmp_path for the backup roots")
    base = Path(tempfile.mkdtemp(prefix="rsched-killed-test-", dir=SHM))
    return base, base / "local" / "rsched-backup", base / "offbox" / "rsched-backup"


def _rows(attempts: Path) -> list[dict]:
    if not attempts.is_file():
        return []
    return [json.loads(line) for line in attempts.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _start(home: Path, root: Path, attempts: Path, rsync: str | None = None,
           overrides: dict[str, str] | None = None) -> subprocess.Popen:
    """The real script, in a session of its OWN so the test can signal its whole group."""
    bin_dir = home.parent / "bin"
    write_executable(bin_dir / "date",
                     f'#!/bin/sh\n[ "$*" = "+%F" ] && {{ echo 2026-11-24; exit 0; }}\n'
                     f'exec {REAL_DATE} "$@"\n')
    if rsync is None:
        (bin_dir / "rsync").unlink(missing_ok=True)
    else:
        write_executable(bin_dir / "rsync", rsync)
    env = {**_env(home), **(overrides or {})}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["BACKUP_LOCK"] = str(home.parent / "backup.lock")
    env["BACKUP_ATTEMPTS_FILE"] = str(attempts)
    env.setdefault("MIRROR_ROOT", "")
    env.setdefault("GC_REPOS", "0")
    assert "/mnt/" not in env["MIRROR_ROOT"] + str(root), "a test may never reach a live share"
    return subprocess.Popen(["bash", str(BACKUP_SH), str(root)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)


def _stop(proc: subprocess.Popen) -> None:
    """End `proc` and CLOSE ITS PIPES. The pipes are the part worth a helper: this suite drives
    the script to a signal rather than to `communicate()`, so the two stream objects are still
    open at teardown — and pytest's `unraisableexception` plugin promotes the resulting
    `ResourceWarning` into a test failure that names no assertion at all (measured 2026-11-24:
    two tests red, both for this and not for anything about the backup).
    """
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def _wait_for(predicate, timeout: float = 60.0, interval: float = 0.25):
    """Poll `predicate` until it answers truthily, and return that answer."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        answer = predicate()
        if answer:
            return answer
        time.sleep(interval)
    return predicate()


def test_a_night_killed_at_the_units_bound_is_recorded_as_killed(home, tmp_path):
    """THE DEFECT THIS FIXES. The off-box tier is inside its rsync when systemd's timeout fires,
    and the record must say so — with the tier, the elapsed seconds and the cause — instead of
    holding only the local tier's row and leaving the night indistinguishable from one where the
    off-box step never ran."""
    base, local, offbox = _roots(tmp_path)
    attempts = tmp_path / "attempts.jsonl"
    proc = _start(home, local, attempts, rsync=HANGING_OFFBOX_RSYNC,
                  overrides={"MIRROR_ROOT": str(offbox)})
    try:
        # Wait until the off-box rsync is the thing running: its own row cannot exist yet, and the
        # local tier's must, or the signal would be testing a different moment.
        assert _wait_for(lambda: [r for r in _rows(attempts) if r["tier"] == "local"]), \
            "the local tier never completed, so this test never reached the off-box attempt"
        assert _wait_for((offbox / ".rsched-backup-started").is_file), \
            "the off-box attempt never started, so the signal would land somewhere else"
        os.killpg(proc.pid, signal.SIGTERM)          # exactly what systemd's KillMode does
        proc.wait(timeout=60)
        killed = _wait_for(lambda: [r for r in _rows(attempts) if r["outcome"] == "killed"])
        assert killed, f"a killed night recorded no row: {_rows(attempts)}"
        row = killed[-1]
        assert row["tier"] == "offbox", row
        assert row["elapsed_s"] >= 0, row
        assert "SIGTERM" in row["note"], row
        # And the unit must still see a failure: a handler that swallowed the signal would turn a
        # killed night into a reported success, which is worse than not recording it.
        assert proc.returncode != 0, "the script exited 0 after being terminated"
    finally:
        _stop(proc)
        shutil.rmtree(base, ignore_errors=True)


def test_the_local_tier_is_not_blamed_for_a_night_killed_off_box(home, tmp_path):
    """One row per tier per attempt: the local tier records its OWN completion and the killed row
    names the tier that was actually running, so a reader is never told the completed local
    snapshot was cut short. What this does NOT prove, measured red-first on 2026-11-24 rather
    than assumed: removing the marker-clearing at the LOCAL tier's record site leaves this test
    green, because the off-box tier overwrites the marker when its own attempt starts — the
    double-record property is proven by `test_every_record_site_clears_the_in_flight_marker`,
    which reads the sites themselves, while this one proves the ATTRIBUTION.
    """
    base, local, offbox = _roots(tmp_path)
    attempts = tmp_path / "attempts.jsonl"
    proc = _start(home, local, attempts, rsync=HANGING_OFFBOX_RSYNC,
                  overrides={"MIRROR_ROOT": str(offbox)})
    try:
        assert _wait_for(lambda: [r for r in _rows(attempts) if r["tier"] == "local"])
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=60)
        _wait_for(lambda: [r for r in _rows(attempts) if r["outcome"] == "killed"])
        rows = _rows(attempts)
        local_rows = [r for r in rows if r["tier"] == "local"]
        assert len(local_rows) == 1, f"the local tier was recorded twice: {rows}"
        assert local_rows[0]["outcome"] == "complete", local_rows
    finally:
        _stop(proc)
        shutil.rmtree(base, ignore_errors=True)


def test_a_refused_offbox_target_is_recorded_rather_than_silent(home, tmp_path):
    """An unmounted share is refused before any rsync, and that refusal had no record site: the
    file then reads exactly as it does for a night where no off-box tier was configured. A reader
    has to be able to tell "the share was not there" from "there is nothing to say"."""
    base, local, _ = _roots(tmp_path)
    attempts = tmp_path / "attempts.jsonl"
    doomed = tmp_path / "not-a-disk" / "rsched-backup"          # on the HOME's own filesystem
    proc = _start(home, local, attempts, overrides={"MIRROR_ROOT": str(doomed)})
    try:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, f"an off-box refusal must not fail the night:\n{err}"
        rows = _rows(attempts)
        refused = [r for r in rows if r["outcome"] == "refused"]
        assert refused, f"a refused off-box target recorded nothing: {rows}"
        assert refused[-1]["tier"] == "offbox", refused
        assert "mounted" in refused[-1]["note"], refused
    finally:
        _stop(proc)
        shutil.rmtree(base, ignore_errors=True)


def test_the_clause_a_run_reads_names_a_cut_short_night_for_what_it_is(tmp_path):
    """The point of the record is the sentence a run can read. `killed` has to reach it with its
    cause, because "failed after 21600s" and "killed at the unit's bound after 21600s" lead to
    different remedies — one is a broken share, the other a bound that is too short."""
    path = tmp_path / ".control" / ATTEMPTS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": "2026-11-24T07:13:20+01:00", "tier": "offbox",
                                "outcome": "killed", "elapsed_s": 21479,
                                "note": "SIGTERM — the attempt was cut short, not finished "
                                        "(the unit's own bound)"}) + "\n", encoding="utf-8")
    clause = attempt_detail("off-box", tmp_path)
    assert "killed" in clause
    assert "21479s" in clause
    assert "cut short" in clause


def test_every_record_site_clears_the_in_flight_marker():
    """The marker is what the handler reads, so a site that records a row and leaves the marker
    set would have the handler record the SAME attempt twice. Four sites write a row for an
    attempt that is in flight; each must close it."""
    source = BACKUP_SH.read_text(encoding="utf-8")
    for call in ('record_attempt local complete "${LOCAL_STARTED_AT}"',
                 'record_attempt offbox "failed" "${started_at}" "" "rsync exit ${rc}"',
                 'record_attempt offbox complete "${started_at}" "${sent:-}"'):
        after = source[source.index(call) + len(call):][:300]
        assert 'ATTEMPT_IN_FLIGHT_AT=""' in after, (
            f"the site `{call}` does not clear the in-flight marker, so a signal arriving "
            f"afterwards records the same attempt a second time")


def test_the_handler_re_raises_the_signal_it_handled():
    """A trap that returns normally makes bash exit 0, and systemd would read a killed night as a
    successful one — no OnFailure push, no failed unit, and the health watch left to notice the
    staleness days later. The handler removes itself and re-signals its own process."""
    source = BACKUP_SH.read_text(encoding="utf-8")
    body = source[source.index("on_terminated() {"):source.index("trap 'on_terminated TERM' TERM")]
    assert 'trap - "${signal}"' in body
    assert 'kill -"${signal}" "$$"' in body
