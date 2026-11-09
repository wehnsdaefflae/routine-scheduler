"""`deploy/backup.sh` never mistakes a held lock for a snapshot taken.

On 2026-11-09 the 01:09 nightly found its lock held — by a release gate's own backup tests, which
ran the real script on the default `/tmp` lock that the engine container shares with the host —
and exited 0 with "nothing to do". systemd recorded a success, the failure push never fired, and
no snapshot existed for that night. Now a contended run waits `LOCK_WAIT_S` and then FAILS, and
`BACKUP_LOCK` lets every test take a lock of its own.
"""
from __future__ import annotations

import fcntl
import subprocess
import threading
from pathlib import Path

from test_deploy_state import DEPLOY, _env, _home

BUSY = 75  # EX_TEMPFAIL: the script's exit when the lock never came free


def _run(tmp_path: Path, lock: Path, wait_s: int) -> subprocess.CompletedProcess:
    home = _home(tmp_path)
    env = {**_env(home), "BACKUP_LOCK": str(lock), "LOCK_WAIT_S": str(wait_s),
           "MIRROR_ROOT": ""}  # never the live off-box share
    return subprocess.run(["bash", str(DEPLOY / "backup.sh"), str(tmp_path / "root")], env=env,
                          capture_output=True, text=True, timeout=60, check=False)


def test_a_lock_held_past_the_wait_fails_the_run(tmp_path):
    lock = tmp_path / "backup.lock"
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        proc = _run(tmp_path, lock, wait_s=1)
    assert proc.returncode == BUSY, proc.stdout + proc.stderr
    assert "took NO snapshot" in proc.stderr
    assert not (tmp_path / "root").exists()


def test_a_lock_freed_within_the_wait_is_taken(tmp_path):
    lock = tmp_path / "backup.lock"
    held = lock.open("w")
    fcntl.flock(held, fcntl.LOCK_EX)
    threading.Timer(1.0, held.close).start()
    proc = _run(tmp_path, lock, wait_s=20)
    assert proc.returncode != BUSY, proc.stdout + proc.stderr
    assert "took NO snapshot" not in proc.stderr
