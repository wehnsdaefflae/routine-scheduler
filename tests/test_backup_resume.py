"""`deploy/backup.sh` turns a run cut short into progress, never into the next run's debt.

From 2026-10-24 the nightly unit was killed at its time limit every night. The script deleted the
killed run's half-built folder FIRST, inside the next run's limit — and on the sshfs share an
`rm -rf` is one round trip per file, so that delete alone cost 13 to 73 minutes and the next run
was killed too. Three properties break that loop, each driven through the real script the way
`tests/test_backup_snapshots.py` drives it (same HOME, same root on a second filesystem):

- a run CARRIES ON in the folder an interrupted one left, and still ends with exactly the home;
- every delete (a same-day rerun's old copy, retention, a killed run's leftovers) happens only
  after the night's snapshot took its date and the completion stamp was written;
- every run stamps its ATTEMPT first, which is how `daemon/backup_watch` tells a killed night the
  morning after (tests/test_backup_watch.py).
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import pytest

from helpers import write_executable
from test_backup_snapshots import (
    REAL_RSYNC,
    _backup,
    _days,
    _leftovers,
    _ok,
    _plant,
    _snapshots,
    share_root,
)
from test_deploy_state import _home

REAL_RM = shutil.which("rm")


@pytest.fixture
def home(tmp_path) -> Path:
    return _home(tmp_path)


@pytest.fixture
def root(tmp_path) -> Iterator[Path]:
    with share_root(tmp_path) as built:
        yield built


def test_a_run_carries_on_in_what_an_interrupted_one_left_and_ends_with_exactly_the_home(
        home, root):
    """What the killed run already transferred is kept as it stands (the planted copy of a file
    that changed since the base keeps its own inode, so it was not copied again); what it held
    that the home does not — a file deleted since, rsync's half-written temp file, a folder an
    exclude names — is gone. (An unchanged file the leftover holds as a plain copy is swapped for
    a link into the base: rsync prefers the link, and a killed run's are links already.)"""
    _ok(_backup(home, root, "2026-09-30"))
    (home / "conversations/data.txt").write_text("changed after the base", encoding="utf-8")
    half = root / "snapshots/.in-progress"
    (half / "routines/.venv").mkdir(parents=True)
    (half / "routines/.venv/site.py").write_text("excluded bulk", encoding="utf-8")
    (half / "routines/deleted-since.txt").write_text("the home lost this", encoding="utf-8")
    (half / "routines/.data.txt.k3Xq9Z").write_text("half a transfer", encoding="utf-8")
    (half / "conversations").mkdir()
    done_already = half / "conversations/data.txt"
    shutil.copy2(home / "conversations/data.txt", done_already)   # same bytes, same mtime
    planted_inode = done_already.stat().st_ino

    proc = _backup(home, root, "2026-10-01")
    _ok(proc)
    assert "carrying on in the folder an interrupted run left" in proc.stdout
    assert _snapshots(root) == ["2026-09-30", "2026-10-01"]
    assert _leftovers(root) == []
    tonight = root / "snapshots/2026-10-01"
    assert (tonight / "conversations/data.txt").stat().st_ino == planted_inode
    for gone in (".venv", "deleted-since.txt", ".data.txt.k3Xq9Z"):
        assert not (tonight / "routines" / gone).exists(), gone
    assert (tonight / "routines/data.txt").stat().st_ino == \
        (root / "snapshots/2026-09-30/routines/data.txt").stat().st_ino
    assert (root / "snapshots/2026-09-30/routines/data.txt").read_text(encoding="utf-8") == \
        "routines"


def test_nothing_is_deleted_before_the_snapshot_took_its_date(home, root):
    """The deletes are the slow part on the share, so none may stand between a run and its
    snapshot: a killed run's discards and tonight's retention both go after the stamp."""
    if REAL_RM is None:
        pytest.skip("no rm on this host")
    _plant(root, *_days(date(2026, 8, 1), date(2026, 9, 30)))   # retention prunes most of these
    leftover = root / "snapshots/.discard/2026-07-01.4242"       # a killed run's, mid-delete
    leftover.mkdir(parents=True)
    (leftover / "planted.txt").write_text("going", encoding="utf-8")
    log = home.parent / "rm.log"
    write_executable(home.parent / "bin/rm", (
        '#!/bin/sh\nfor a; do case "$a" in *.discard*|*.in-progress*)\n'
        f'  if [ -d "{root}/snapshots/2026-10-01" ] && [ -s "{root}/.rsched-backup-completed" ]\n'
        f'  then echo "after $a" >> "{log}"; else echo "BEFORE $a" >> "{log}"; fi ;;\n'
        f'esac; done\nexec {REAL_RM} "$@"\n'))
    try:
        _ok(_backup(home, root, "2026-10-01"))
    finally:
        (home.parent / "bin/rm").unlink()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls and all(c.startswith("after ") for c in calls), calls
    assert _leftovers(root) == []
    assert "2026-10-01" in _snapshots(root) and "2026-08-01" not in _snapshots(root)


def test_every_run_stamps_its_attempt_before_its_completion(home, root):
    """The attempt stamp is written first and the completion stamp last, so a run killed between
    them leaves an attempt NEWER than the last completion — the shape backup_watch reads as a failed
    night. A failed home leaves exactly that shape too."""
    def stamp(name: str) -> datetime:
        return datetime.fromisoformat((root / name).read_text(encoding="utf-8").strip())

    _ok(_backup(home, root, "2026-09-30"))
    assert stamp(".rsched-backup-started") <= stamp(".rsched-backup-completed")

    fails =(f'#!/bin/sh\ncase "$*" in *"/./routines/"*) exit 23 ;; esac\n'
             f'exec {REAL_RSYNC} "$@"\n')
    (root / ".rsched-backup-completed").write_text("2026-09-30T04:00:00+00:00\n",
                                                   encoding="utf-8")
    assert _backup(home, root, "2026-10-01", rsync=fails).returncode == 1
    assert stamp(".rsched-backup-started") > stamp(".rsched-backup-completed")
