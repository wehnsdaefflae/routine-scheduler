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

WHICH READER ACTUALLY HITS THE REFUSED BRANCH, measured 2026-11-07 rather than assumed — because
a fix that only ever fires for a reader this code does not have would be inert while looking
shipped:

* the DAEMON (`daemon/scheduler.BackupWatch`) runs as **uid 1000**, and `/mnt/usb500` is
  `drwx------ uid=1000` — it OWNS the mount point, and the Landlock jail wraps run subprocesses,
  not the daemon itself. So the daemon is not refused: for its tick an `unknown` local tier means
  the stamp is genuinely ABSENT, and the finding there belongs to `backup.sh` not having
  completed, not to this module.
* a ROUTINE RUN reading the same root — and `cli_postdeploy` invoked from inside one — gets
  EACCES on the open while `os.stat` still answers, which is the conflation that produced
  `postdeploy.jsonl`'s wrong sentence and R3234.

So the split's value is: the sentence stops ASSERTING one cause out of three, and the two readers
stop being told the same thing when they are standing in different places. The next
`backup_stale` event the daemon files is itself the instrument that confirms which branch it
takes — if it still reads "may not be mounted", the local tier's stamp really is absent.
"""

from __future__ import annotations

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
    root.chmod(0o000)
    try:
        when, which, blocked = backup_watch.newest_stamp(
            backup_watch.COMPLETED_STAMP, [root])
    finally:
        root.chmod(0o700)

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
    root.chmod(0o000)
    monkeypatch.setattr(backup_watch, "backup_tiers", lambda env=None: [("local", root)])
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [root])
    try:
        state, detail = backup_watch.staleness()
    finally:
        root.chmod(0o700)

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


@pytest.mark.parametrize("refuse", ["the directory", "the stamp file"])
def test_both_a_refused_directory_and_a_refused_file_are_blocked(tmp_path, refuse):
    """A refused directory and a refused FILE both mean the same thing here — this process cannot
    look — and they reach `read_text` by DIFFERENT errno paths: the path traversal, and the open
    itself. Either way the reader must be told about the ROOT, which is the thing a person can
    act on.

    What is deliberately NOT a case here, because it asserts a refusal that cannot happen: mode
    `0o100` on the directory. The first draft of this file parametrized it beside `0o000` and got
    an empty `blocked` — a directory carrying only the execute bit is TRAVERSABLE, so the open of
    a mode-0o644 stamp inside it SUCCEEDS. Reading a stamp never lists its directory, so the
    directory's READ bit is irrelevant to this code path.
    """
    root = tmp_path / "usb"
    stamp = _stamp(root)
    target = root if refuse == "the directory" else stamp
    target.chmod(0o000)
    try:
        _when, _which, blocked = backup_watch.newest_stamp(
            backup_watch.COMPLETED_STAMP, [root])
    finally:
        target.chmod(0o700)

    assert blocked, f"{refuse} was unreadable and newest_stamp reported nothing blocked"
    assert [p for p, _ in blocked] == [root]
