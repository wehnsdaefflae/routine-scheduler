"""A backup stamp this process may not LOOK at is not an absent one — the operator's decision."""

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
