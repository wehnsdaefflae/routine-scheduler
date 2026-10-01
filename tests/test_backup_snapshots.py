"""`deploy/backup.sh` keeps DATED SNAPSHOTS: one folder per night, unchanged files hard-linked.

The single converging mirror it replaced copied damage over the last good copy within a night —
a run that wrecked state at 01:00 was mirrored at 03:30, and a deleted conversation lived only
until the next run. Every run now writes `<root>/snapshots/<YYYY-MM-DD>` with `rsync
--link-dest` against the newest complete snapshot, under a temporary name it renames only once
every home copied, and then prunes to 14 daily + 8 weekly. Each property here is one the shell
can break without a sound: a half snapshot kept or linked against, the snapshot just written
pruned, a gigabytes-large mirror copied instead of moved. So each is driven through the real
script, against a throwaway HOME and a root on a second filesystem — the script refuses a root
on its home's device (the unmounted-share guard), which is why the root lives on /dev/shm.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from test_deploy_state import DEPLOY, EXCLUDED, _bundle, _env, _home, _tar_files

SHM = Path("/dev/shm")  # noqa: S108 — a second FILESYSTEM is the point; mkdtemp makes the dir
REAL_DATE, REAL_RSYNC = shutil.which("date"), shutil.which("rsync")
#: The script's own lock is host-wide, and a contended run exits 0 having done nothing — so the
#: tests serialize their runs on a lock of their OWN, or two xdist workers would skip each
#: other's runs. Only a real backup holding the script's lock still skips a test.
TEST_LOCK = Path(tempfile.gettempdir()) / "rsched-backup-tests.lock"


@pytest.fixture
def home(tmp_path) -> Path:
    return _home(tmp_path)


@pytest.fixture
def root(tmp_path) -> Iterator[Path]:
    """The backup root, on a filesystem apart from tmp_path (where the HOME lives)."""
    if REAL_RSYNC is None:
        pytest.skip("backup.sh copies with rsync, which this host does not have")
    if not SHM.is_dir() or not os.access(SHM, os.W_OK) \
            or SHM.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("needs a writable filesystem apart from tmp_path for the backup root")
    share = Path(tempfile.mkdtemp(prefix="rsched-backup-test-", dir=SHM))
    try:
        yield share / "rsched-backup"
    finally:
        shutil.rmtree(share, ignore_errors=True)


def _stub(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _backup(home: Path, root: Path, today: str, rsync: str | None = None,
            ) -> subprocess.CompletedProcess:
    """One run of the real script on the day `today` — a stub answers `date +%F` and hands every
    other call to the real date — optionally through an `rsync` stub."""
    bin_dir = home.parent / "bin"
    _stub(bin_dir / "date",
          f'#!/bin/sh\n[ "$*" = "+%F" ] && {{ echo {today}; exit 0; }}\nexec {REAL_DATE} "$@"\n')
    if rsync is None:
        (bin_dir / "rsync").unlink(missing_ok=True)
    else:
        _stub(bin_dir / "rsync", rsync)
    env = _env(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    with TEST_LOCK.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        proc = subprocess.run(["bash", str(DEPLOY / "backup.sh"), str(root)], env=env,
                              capture_output=True, text=True, timeout=120, check=False)
    if "another backup is already running" in proc.stdout:
        pytest.skip("a real backup holds the script's lock")
    return proc


def _ok(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _snapshots(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "snapshots").iterdir() if not p.name.startswith("."))


def _leftovers(root: Path) -> list[str]:
    """Anything a run builds or discards under a temporary name — none may outlive the run."""
    return sorted(p.name for p in (root / "snapshots").iterdir() if p.name.startswith("."))


def _latest(root: Path) -> str:
    return str((root / "latest").readlink())


def _plant(root: Path, *days: str) -> None:
    """Snapshots as earlier runs would have left them (one file each, nothing to link)."""
    for day in days:
        (root / "snapshots" / day).mkdir(parents=True)
        (root / "snapshots" / day / "planted.txt").write_text(day, encoding="utf-8")


def _days(first: date, last: date) -> list[str]:
    return [(first + timedelta(n)).isoformat() for n in range((last - first).days + 1)]


def test_every_night_is_a_dated_folder_and_an_unchanged_file_is_one_inode(home, root):
    """What makes keeping a snapshot per night affordable: a file that did not change is a hard
    link to the previous snapshot's copy rather than a second copy. And what makes keeping them
    worth it: a file that DID change is a new file, so the earlier night still holds what it had."""
    _ok(_backup(home, root, "2026-09-30"))
    assert _snapshots(root) == ["2026-09-30"]
    assert _latest(root) == "snapshots/2026-09-30"
    (home / "routines/data.txt").write_text("rewritten after the first night", encoding="utf-8")

    proc = _backup(home, root, "2026-10-01")
    _ok(proc)
    assert "files unchanged since 2026-09-30 are hard-linked" in proc.stdout
    assert _snapshots(root) == ["2026-09-30", "2026-10-01"]
    assert _latest(root) == "snapshots/2026-10-01"
    old, new = root / "snapshots/2026-09-30", root / "snapshots/2026-10-01"
    kept = (old / "conversations/data.txt").stat(), (new / "conversations/data.txt").stat()
    assert kept[0].st_ino == kept[1].st_ino
    assert kept[1].st_nlink == 2
    assert (old / "routines/data.txt").stat().st_ino != (new / "routines/data.txt").stat().st_ino
    assert (old / "routines/data.txt").read_text(encoding="utf-8") == "routines"
    assert (new / "routines/data.txt").read_text(encoding="utf-8") == \
        "rewritten after the first night"
    assert _leftovers(root) == []


def test_a_second_run_the_same_day_refreshes_that_days_snapshot(home, root):
    """`docker compose stop chrome && deploy/backup.sh` — the script's own advice for a clean
    browser copy — is a second run on the same date. It replaces that day's snapshot rather than
    adding a second one, and the replaced copy goes without disturbing the files it shared."""
    _ok(_backup(home, root, "2026-09-30"))
    _ok(_backup(home, root, "2026-10-01"))
    (home / "routines/data.txt").write_text("changed between the two runs", encoding="utf-8")
    _ok(_backup(home, root, "2026-10-01"))

    assert _snapshots(root) == ["2026-09-30", "2026-10-01"]
    assert _leftovers(root) == []
    assert _latest(root) == "snapshots/2026-10-01"
    old, new = root / "snapshots/2026-09-30", root / "snapshots/2026-10-01"
    assert (new / "routines/data.txt").read_text(encoding="utf-8") == "changed between the two runs"
    assert (old / "conversations/data.txt").stat().st_ino == \
        (new / "conversations/data.txt").stat().st_ino


def test_a_failed_run_keeps_no_half_snapshot_moves_no_latest_and_prunes_nothing(home, root):
    """The temporary name is the whole guarantee: a run that could not copy every home never
    gives its folder a date, so no restore can pick it and no later run links against it. And
    retention is the success path's last step — a failed night deletes nothing at all."""
    _ok(_backup(home, root, "2026-09-30"))
    older = _days(date(2026, 8, 1), date(2026, 8, 31))   # retention would prune most of these
    _plant(root, *older)
    fails_one_home = (f'#!/bin/sh\ncase "$*" in *"/./conversations/"*)\n'
                      f'  echo "rsync: [sender] change_dir failed: simulated" >&2; exit 23 ;;\n'
                      f'esac\nexec {REAL_RSYNC} "$@"\n')

    proc = _backup(home, root, "2026-10-01", rsync=fails_one_home)
    assert proc.returncode == 1
    assert "INCOMPLETE" in proc.stderr and "conversations" in proc.stderr
    assert "simulated" in proc.stdout                    # the reason, not the byte counts
    assert _snapshots(root) == sorted([*older, "2026-09-30"])
    assert _leftovers(root) == []
    assert _latest(root) == "snapshots/2026-09-30"

    _ok(_backup(home, root, "2026-10-01"))               # the next good run is unaffected
    assert _latest(root) == "snapshots/2026-10-01"


def test_a_run_never_builds_on_what_an_interrupted_one_left(home, root):
    """A run killed mid-copy (the unit's two-hour timeout, a reboot) leaves its half-built folder
    under the temporary name, rsync's own temp files included. The next run deletes it and
    starts from an empty folder: copying INTO the leftover — there is no --delete — would carry
    whatever it held, a file the home has since lost among them, into a snapshot that then
    looks complete."""
    _ok(_backup(home, root, "2026-09-30"))
    half = root / "snapshots/.in-progress/routines"
    half.mkdir(parents=True)
    (half / "deleted-since.txt").write_text("the home no longer has this", encoding="utf-8")
    (half / ".data.txt.k3Xq9Z").write_text("half a transfer", encoding="utf-8")
    (root / "snapshots/.discard/routines").mkdir(parents=True)

    _ok(_backup(home, root, "2026-10-01"))
    assert _leftovers(root) == []
    assert _snapshots(root) == ["2026-09-30", "2026-10-01"]
    tonight = root / "snapshots/2026-10-01/routines"
    assert not (tonight / "deleted-since.txt").exists()
    assert not (tonight / ".data.txt.k3Xq9Z").exists()
    assert (tonight / "data.txt").stat().st_ino == \
        (root / "snapshots/2026-09-30/routines/data.txt").stat().st_ino


def test_a_host_without_key_files_is_backed_up(home, root):
    """`~/.credentials` is the credential ladder's optional last rung — install.sh never creates
    it — so its absence is a fresh install, not a broken one: the snapshot carries what the host
    has and says what it skipped, exactly as the tarball does."""
    shutil.rmtree(home / ".credentials")
    proc = _backup(home, root, "2026-10-01")
    _ok(proc)
    assert "skipping .credentials" in proc.stderr
    assert _snapshots(root) == ["2026-10-01"]
    assert not (root / "snapshots/2026-10-01/.credentials").exists()


def test_files_that_vanish_mid_copy_still_complete_the_snapshot(home, root):
    """rsync exit 24: a file listed, then deleted before its copy — an atomic write's tmp, a
    LevelDB compaction in the live chrome profile. A snapshot is all-or-nothing, so failing on
    it would lose the whole night for a file that no longer exists."""
    vanishes = (f'#!/bin/sh\n{REAL_RSYNC} "$@" || exit $?\ncase "$*" in *"/./routines/"*)\n'
                f'  echo \'file has vanished: "/home/routines/r1/.state.json.tmp"\' >&2\n'
                f'  exit 24 ;;\nesac\n')
    proc = _backup(home, root, "2026-10-01", rsync=vanishes)
    _ok(proc)
    assert "1 vanished mid-copy" in proc.stdout
    assert _snapshots(root) == ["2026-10-01"]
    assert (root / "snapshots/2026-10-01/routines/data.txt").is_file()


def test_retention_keeps_14_daily_and_the_newest_of_8_weeks_beyond(home, root):
    """A night per day for two weeks, then a week per snapshot for eight more. The weeks are ISO
    weeks that HOLD a snapshot — the two weeks this host was down cost no depth — and a week's
    keeper is its newest snapshot, whichever weekday that fell on."""
    down = {*_days(date(2026, 8, 10), date(2026, 8, 23)), "2026-08-01", "2026-08-02"}
    planted = [d for d in _days(date(2026, 6, 1), date(2026, 9, 30)) if d not in down]
    _plant(root, *planted, "2026-02-30")                 # date-shaped, not a date: left alone

    proc = _backup(home, root, "2026-10-01")
    _ok(proc)
    daily = _days(date(2026, 9, 18), date(2026, 10, 1))
    weekly = ["2026-07-19", "2026-07-26",
              "2026-07-31",                              # W31's newest: its weekend is missing
              "2026-08-09",                              # W32 — W33 and W34 hold no snapshot
              "2026-08-30", "2026-09-06", "2026-09-13",
              "2026-09-17"]                              # W38, the week the 14 days cut into
    assert len(daily) == 14 and len(weekly) == 8
    assert _snapshots(root) == sorted([*daily, *weekly, "2026-02-30"])
    assert "14 daily + 8 weekly kept" in proc.stdout
    assert _leftovers(root) == []


def test_a_clock_set_back_never_prunes_the_snapshot_it_just_wrote(home, root):
    """Retention reads dates from folder names, so snapshots stamped by a clock running ahead
    rank as the newest. However they rank, the one this run just wrote stays, and it is the one
    `latest` names."""
    _plant(root, *_days(date(2026, 9, 29), date(2026, 10, 13)))
    _ok(_backup(home, root, "2026-09-28"))
    assert "2026-09-28" in _snapshots(root)
    assert _latest(root) == "snapshots/2026-09-28"


@pytest.mark.parametrize(("completed", "snapshots"), [
    ("2026-09-29T03:41:07+02:00\n", ["2026-09-29", "2026-10-01"]),
    (None, ["2026-10-01"]),          # never completed: no date to give, so it is today's base
])
def test_the_old_single_mirror_is_moved_into_the_first_snapshot(home, root, completed,
                                                                 snapshots):
    """MIGRATION(expires=2026-11-15): until now the root held one mirror, every home directly
    under it. The first run MOVES it into a snapshot — a rename, instant at gigabytes — dated
    by the run that last completed it, and links tonight's against it instead of copying
    everything again. What is not an inventory home stays where it is."""
    shutil.copytree(home, root, symlinks=True)           # the mirror: every home at the root
    if completed is not None:
        (root / ".rsched-backup-completed").write_text(completed, encoding="utf-8")
    (root / "lost+found").mkdir()
    mirrored_inode = (root / "routines/data.txt").stat().st_ino

    proc = _backup(home, root, "2026-10-01")
    _ok(proc)
    assert "migrated" in proc.stdout
    assert _snapshots(root) == snapshots
    assert _leftovers(root) == []
    assert _latest(root) == "snapshots/2026-10-01"
    assert sorted(p.name for p in root.iterdir()) == \
        [".rsched-backup-completed", "latest", "lost+found", "snapshots"]
    # moved, not copied — and the night's snapshot links to it rather than copying it again
    assert (root / "snapshots" / snapshots[0] / "routines/data.txt").stat().st_ino \
        == mirrored_inode
    assert (root / "snapshots/2026-10-01/routines/data.txt").stat().st_ino == mirrored_inode


@pytest.mark.parametrize("moved_before_the_cut", [("routines", "conversations"), "every home"])
def test_a_migration_cut_short_resumes_instead_of_stranding_half(home, root, moved_before_the_cut):
    """MIGRATION(expires=2026-11-15): the move goes through `snapshots/.migrating` and takes its
    date last, so a run killed between two renames is finished by the next one — including when
    every home had already moved, and nothing at the root still looks like the old layout."""
    shutil.copytree(home, root, symlinks=True)
    (root / ".rsched-backup-completed").write_text("2026-09-29T03:41:07+02:00\n", encoding="utf-8")
    tops = sorted(p.name for p in home.iterdir())
    moved = tops if moved_before_the_cut == "every home" else moved_before_the_cut
    (root / "snapshots/.migrating").mkdir(parents=True)
    for top in moved:
        (root / top).rename(root / "snapshots/.migrating" / top)

    _ok(_backup(home, root, "2026-10-01"))
    assert _snapshots(root) == ["2026-09-29", "2026-10-01"]
    assert _leftovers(root) == []
    assert sorted(p.name for p in (root / "snapshots/2026-09-29").iterdir()) == tops
    assert sorted(p.name for p in root.iterdir()) == \
        [".rsched-backup-completed", "latest", "snapshots"]


def test_the_tarball_and_the_snapshot_carry_the_same_files(home, root, tmp_path):
    """bundle.sh and backup.sh read ONE exclude list, so they must carry exactly the same
    files. The mirror used to sync each home from inside it, where rsync never sees the
    `git-repos/LLMSecTest_agentic/` prefix an anchored exclude names — so it copied the
    workspace bulk the tarball leaves out. And a file an older snapshot still carries from
    before its exclude existed must not reach tonight's: each snapshot starts empty."""
    out = tmp_path / "bundle.tgz"
    proc = _bundle(home, out)
    assert proc.returncode == 0, proc.stderr
    tarred = _tar_files(out)
    _plant(root, "2026-09-30")
    stale = root / "snapshots/2026-09-30/git-repos/LLMSecTest_agentic/apps/stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("copied before the exclude existed", encoding="utf-8")

    _ok(_backup(home, root, "2026-10-01"))
    tonight = root / "snapshots/2026-10-01"
    copied = {str(p.relative_to(tonight)) for p in tonight.rglob("*")
              if p.is_symlink() or p.is_file()}
    assert not EXCLUDED & tarred, f"the tarball carried excluded files: {EXCLUDED & tarred}"
    assert not EXCLUDED & copied, f"the snapshot carried excluded files: {EXCLUDED & copied}"
    assert "routines/r1/apps/kept.txt" in tarred   # a workspace's exclude stays in its home
    assert copied == tarred, f"only in the snapshot: {copied - tarred}; only tarred: {tarred - copied}"


def test_the_help_says_how_to_restore_without_touching_a_share(tmp_path):
    """`--help` is read when something is already wrong — the share may be down — so it answers
    before the lock, the device check or any write."""
    env = _env(tmp_path)
    proc = subprocess.run(["bash", str(DEPLOY / "backup.sh"), "--help"], env=env,
                          capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "How to restore" in proc.stdout
    assert "rsync -a --delete BACKUP_ROOT/snapshots/<YYYY-MM-DD>/<home>/ ~/<home>/" in proc.stdout
    assert "service stopped" in proc.stdout
    unknown = subprocess.run(["bash", str(DEPLOY / "backup.sh"), "--dry-run"], env=env,
                             capture_output=True, text=True, timeout=30, check=False)
    assert unknown.returncode == 2
    assert "usage:" in unknown.stderr
