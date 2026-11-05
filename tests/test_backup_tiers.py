"""R3204: the nightly snapshot goes to the LOCAL disk first, the off-box share second.

The operator decided this on 2026-11-05 because no complete snapshot had finished since
11-01: the off-box target is sshfs at one round trip per file, and ~167k files cannot finish
inside the unit's `TimeoutStartSec=4h`. A local ext4 disk does the same `--link-dest`
snapshot in minutes. It does not REPLACE the off-box copy — a disk in this box does not
survive losing the box — so the off-box tier runs afterwards, from the newest local snapshot.

What is pinned here is the ORDERING PROPERTY the decision rests on, which is the one a shell
can break silently: the local snapshot is complete and stamped BEFORE the off-box step runs,
and an off-box failure leaves that local snapshot and its stamp exactly as they were. Plus
the refusal of step 3 — a target that is not on a mounted disk of its own — because the
operator asked for it precisely while /mnt/usb500 may not be mounted yet.

Driven through the real script against a throwaway HOME, like its sibling suites.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_backup_snapshots import SHM, _backup, _ok
from test_deploy_state import DEPLOY, _env, _home


@pytest.fixture
def home(tmp_path) -> Path:
    return _home(tmp_path)


def _tiers(tmp_path: Path):
    """Two roots on the /dev/shm filesystem: the local tier and the off-box one."""
    if not SHM.is_dir() or not os.access(SHM, os.W_OK) \
            or SHM.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("needs a writable filesystem apart from tmp_path for the backup roots")
    base = Path(tempfile.mkdtemp(prefix="rsched-tiers-test-", dir=SHM))
    return base, base / "local" / "rsched-backup", base / "offbox" / "rsched-backup"


def test_the_local_tier_is_the_default_and_the_offbox_one_is_second():
    """The defaults carry the decision: a reader of the script sees which tier is which."""
    text = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
    assert "DEFAULT_ROOT=/mnt/usb500/rsched-backup" in text
    assert "DEFAULT_MIRROR_ROOT=/mnt/sshd_volume1/rsched-backup" in text


def test_the_unit_passes_the_local_tier_and_hands_the_offbox_one_through_the_environment():
    """The unit's wiring is the whole order, and getting it backwards would make the NAS the
    nightly tier again — silently, since both paths are valid roots.

    Also: no `$VAR` inside a `bash -c` command. systemd expands `${VAR}` in ExecStart itself, so
    a wrapper's `$RSCHED_MIRROR` would reach bash already empty — the trap
    `test_the_push_commands_dollars_reach_bash_not_systemd` was written for after the ntfy push
    never once arrived.
    """
    unit = (DEPLOY / "rsched-backup.service").read_text(encoding="utf-8")
    assert "Environment=RSCHED_BACKUP_LOCAL=/mnt/usb500/rsched-backup" in unit
    assert "Environment=RSCHED_MIRROR=/mnt/sshd_volume1/rsched-backup" in unit
    exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert exec_line.endswith("${RSCHED_BACKUP_LOCAL}"), exec_line
    assert "bash -c" not in exec_line, exec_line
    # And the script reads the off-box tier from that same variable, so the unit needs no wrapper.
    assert "RSCHED_MIRROR" in (DEPLOY / "backup.sh").read_text(encoding="utf-8")


def test_an_explicit_empty_mirror_root_means_local_only_and_is_not_overridden(home, tmp_path):
    """`MIRROR_ROOT=` is an instruction, not an absence: a caller that says "local tier only"
    must not have the unit's RSCHED_MIRROR filled in behind it."""
    base, local, _ = _tiers(tmp_path)
    try:
        proc = _backup(home, local, "2026-11-05",
                       overrides={"MIRROR_ROOT": "", "RSCHED_MIRROR": "/mnt/somewhere-else"})
        _ok(proc)
        assert "off-box mirror: not configured" in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_both_tiers_complete_and_the_offbox_copy_matches_the_local_snapshot(home, tmp_path):
    base, local, offbox = _tiers(tmp_path)
    try:
        proc = _backup(home, local, "2026-11-05", overrides={"MIRROR_ROOT": str(offbox)})
        _ok(proc)
        day = max(p.name for p in (local / "snapshots").iterdir() if p.name[0].isdigit())
        assert (local / ".rsched-backup-completed").is_file()
        assert (offbox / ".rsched-backup-completed").is_file()
        assert (offbox / "snapshots" / day).is_dir(), "the off-box tier holds the same dated snapshot"
        assert (offbox / "latest").resolve() == (offbox / "snapshots" / day).resolve()
        local_files = {p.relative_to(local / "snapshots" / day)
                       for p in (local / "snapshots" / day).rglob("*") if p.is_file()}
        off_files = {p.relative_to(offbox / "snapshots" / day)
                     for p in (offbox / "snapshots" / day).rglob("*") if p.is_file()}
        assert local_files and local_files == off_files
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_an_offbox_failure_leaves_the_local_snapshot_complete_and_stamped(home, tmp_path):
    """THE PROPERTY THE WHOLE DECISION RESTS ON. The off-box step must be unable to fail or
    delay the local one — so an off-box target that is REFUSED (not a mounted disk of its own)
    must still leave a complete, stamped local snapshot and a zero exit.
    """
    base, local, _ = _tiers(tmp_path)
    try:
        # An off-box root on the HOME's own filesystem: refused by the mount guard.
        doomed = tmp_path / "not-a-disk" / "rsched-backup"
        proc = _backup(home, local, "2026-11-05", overrides={"MIRROR_ROOT": str(doomed)})
        assert proc.returncode == 0, f"an off-box refusal must not fail the night:\n{proc.stderr}"
        day = max(p.name for p in (local / "snapshots").iterdir() if p.name[0].isdigit())
        assert (local / ".rsched-backup-completed").is_file()
        assert (local / "latest").resolve() == (local / "snapshots" / day).resolve()
        assert "REFUSING (off-box tier)" in proc.stderr
        assert not (doomed / "snapshots").exists(), "nothing was written to the refused target"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_target_not_on_a_mounted_disk_of_its_own_is_refused(home, tmp_path):
    """Step 3, for the LOCAL tier: an unplugged drive leaves an ordinary directory behind, and
    a snapshot landing there would fill the root disk and report success.
    """
    doomed = tmp_path / "unplugged" / "rsched-backup"
    proc = _backup(home, doomed, "2026-11-05")
    assert proc.returncode == 1
    assert "NOT ON A MOUNTED DISK OF ITS OWN" in proc.stderr
    assert not (doomed / "snapshots").exists()


def test_the_offbox_step_can_be_skipped_without_touching_the_local_tier(home, tmp_path):
    base, local, offbox = _tiers(tmp_path)
    try:
        proc = _backup(home, local, "2026-11-05",
                        overrides={"MIRROR_ROOT": str(offbox), "SKIP_OFFBOX": "1"})
        _ok(proc)
        assert (local / ".rsched-backup-completed").is_file()
        assert not offbox.exists(), "skipped means not even created"
        assert "off-box mirror: skipped" in proc.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_no_heredoc_in_deploy_substitutes_a_command_when_it_is_printed():
    """A hazard that fired for real on 2026-11-05, not a hypothetical.

    `usage()` prints with an UNQUOTED heredoc, so it can interpolate ${DEFAULT_ROOT} — which
    means a backtick or $(...) in that PROSE runs as a command. A help text mentioning
    `rsched postdeploy` in backticks made `backup.sh --help` actually execute postdeploy and
    append a row to the live postdeploy ledger. Printing help must have no side effects, so
    the prose in these blocks carries no command substitution at all.
    """
    offenders = []
    for script in sorted(DEPLOY.glob("*.sh")):
        lines = script.read_text(encoding="utf-8").splitlines()
        inside = None
        for number, line in enumerate(lines, 1):
            if inside is None:
                stripped = line.strip()
                if stripped.startswith("cat <<") and "'" not in stripped and '"' not in stripped:
                    inside = stripped.split("<<", 1)[1].strip().lstrip("-")
                continue
            if line.strip() == inside:
                inside = None
                continue
            if "`" in line or "$(" in line:
                offenders.append(f"{script.name}:{number}: {line.strip()[:70]}")
    assert not offenders, (
        "an unquoted heredoc command-substitutes its text, so printing help would RUN these:\n"
        + "\n".join(offenders))


def test_help_has_no_side_effects(home, tmp_path):
    """The same hazard, from the outside: --help writes nothing and exits 0."""
    env = _env(home)
    env["MIRROR_ROOT"] = ""
    before = set(Path(env["HOME"]).rglob("*"))
    proc = subprocess.run(["bash", str(DEPLOY / "backup.sh"), "--help"], env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "TWO TIERS" in proc.stdout
    assert set(Path(env["HOME"]).rglob("*")) == before
