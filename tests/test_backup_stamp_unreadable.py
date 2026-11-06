"""A backup stamp this process may not LOOK at is not an absent one — the operator's decision.

He answered on 2026-11-06, verbatim: *"Both: I will do the sudo part AND you build the
ENOENT/EACCES split"*, after this routine reported (R3234) that from inside a run the two are
indistinguishable: `/mnt/usb500` is outside every run's read roots, so `stat` answers while
`open` raises EACCES, and neither `journalctl` nor `systemctl` exists in the engine container.
`postdeploy.jsonl` 2026-11-06T10:00:30 therefore read *"no readable .rsched-backup-completed
under any backup root (/mnt/usb500/rsched-backup) — the share may not be mounted where this
process can see it"* — which names one cause out of three and gets the local tier's real one
wrong: the disk IS mounted (`findmnt -T` says `/dev/sdb1 ext4`) and the directory IS owned by the
daemon's uid.

`newest_stamp` swallowed every `OSError` into `continue`, so "the file is not there" and "I am
not allowed to read it" produced identical output. They call for opposite actions: the first
means the backup did not run, the second means nothing about the backup at all and everything
about where this process is standing.

What does NOT change: `cli_postdeploy.backup_freshness` still FAILs on `unknown`, deliberately —
that command is a person standing at the box he just deployed, and "I could not read the record"
is a finding for him even when it is not evidence that a backup failed. The gain is that the
DETAIL now tells him which of the two he is looking at, so he reaches for `sudo` or for the unit
log rather than guessing.
"""

from __future__ import annotations

import os

import pytest

from rsched.daemon import backup_watch


def _stamp(root, text: str = "2026-11-06T01:00:00+01:00"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / backup_watch.COMPLETED_STAMP
    path.write_text(text, encoding="utf-8")
    return path


def test_a_stamp_that_cannot_be_read_is_reported_as_unreadable_not_as_absent(tmp_path):
    """The defect: an EACCES and an ENOENT produced the same (None, None)."""
    root = tmp_path / "usb"
    _stamp(root)
    os.chmod(root, 0o000)        # noqa: S103 — the point is that the read is refused
    try:
        when, which, blocked = backup_watch.newest_stamp(
            backup_watch.COMPLETED_STAMP, [root])
    finally:
        os.chmod(root, 0o700)

    assert when is None and which is None
    assert blocked, "an unreadable root reported nothing about WHY it could not be read"
    assert root in [p for p, _ in blocked]


def test_an_absent_stamp_reports_nothing_blocked(tmp_path):
    """The other half: a root that simply has no stamp is NOT 'unreadable'. Without this the
    split would read every missing backup as a permission problem, which is the same conflation
    pointing the other way."""
    root = tmp_path / "empty"
    root.mkdir()

    when, which, blocked = backup_watch.newest_stamp(backup_watch.COMPLETED_STAMP, [root])

    assert (when, which) == (None, None)
    assert not blocked


def test_the_verdict_detail_says_which_of_the_two_it_hit(tmp_path, monkeypatch):
    """The sentence the operator actually reads, in `postdeploy.jsonl` and in the `backup_stale`
    health event. `unknown` is the right STATE either way — nothing is claimed about the backup —
    so the distinction has to live in the detail.
    """
    root = tmp_path / "usb"
    _stamp(root)
    os.chmod(root, 0o000)        # noqa: S103
    monkeypatch.setattr(backup_watch, "backup_tiers", lambda env=None: [("local", root)])
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [root])
    try:
        state, detail = backup_watch.staleness()
    finally:
        os.chmod(root, 0o700)

    assert state == "unknown"
    assert "could not be read" in detail
    assert str(root) in detail
    # and it must NOT assert the mount theory, which is what it used to say for this case
    assert "may not be mounted" not in detail


def test_an_absent_stamp_keeps_its_own_wording(tmp_path, monkeypatch):
    """The unmounted/never-ran case keeps the sentence it had — it is still the common one."""
    root = tmp_path / "empty"
    root.mkdir()
    monkeypatch.setattr(backup_watch, "backup_tiers", lambda env=None: [("local", root)])
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [root])

    state, detail = backup_watch.staleness()

    assert state == "unknown"
    assert "may not be mounted" in detail
    assert "could not be read" not in detail


@pytest.mark.parametrize("mode", [0o000, 0o100])
def test_a_root_whose_directory_cannot_be_entered_is_blocked_too(tmp_path, mode):
    """A refused directory and a refused FILE both raise on the read, and both mean the same
    thing here: this process cannot look. Parametrized because the two reach `read_text` by
    different errno paths (the open itself, and the traversal)."""
    root = tmp_path / "usb"
    _stamp(root)
    os.chmod(root, mode)         # noqa: S103
    try:
        _when, _which, blocked = backup_watch.newest_stamp(
            backup_watch.COMPLETED_STAMP, [root])
    finally:
        os.chmod(root, 0o700)

    assert blocked
