"""D299: what a backup attempt cost, where a RUN can read it. The operator answered D299
with (b) — "grant a run the means to diagnose it" — against a card that said no run could do
more than report the staleness. The cheaper half of that grant is a READABLE RECORD:
`deploy/backup.sh` appends one JSON line per attempt per tier under the routines home's
`.control/`, and the stale health event names what the last attempt cost instead of pointing
at a journal a run cannot open.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rsched.daemon import backup_watch
from rsched.daemon.backup_attempts import (
    ATTEMPTS_FILE,
    attempt_detail,
    attempts_path,
    newest_attempt,
)

REPO = Path(__file__).resolve().parents[1]
BACKUP_SH = REPO / "deploy" / "backup.sh"


def _write(home: Path, rows: list[dict]) -> None:
    path = home / ".control" / ATTEMPTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_the_newest_attempt_of_a_tier_is_the_one_read(tmp_path):
    """Each tier is diagnosed on its own attempts: the off-box tier is the one that fails while
    the local one completes nightly, so reading the newest row of the FILE would answer about the
    wrong tier almost every time."""
    _write(tmp_path, [
        {"at": "2026-11-23T01:00:00+01:00", "tier": "offbox", "outcome": "failed",
         "elapsed_s": 14400},
        {"at": "2026-11-23T02:00:00+01:00", "tier": "local", "outcome": "complete",
         "elapsed_s": 62},
    ])
    assert newest_attempt("offbox", tmp_path)["outcome"] == "failed"
    assert newest_attempt("local", tmp_path)["elapsed_s"] == 62


def test_a_corrupt_line_is_skipped_rather_than_raised(tmp_path):
    """A half-written line is what a killed backup leaves behind, which is exactly when this is
    read. Raising here would stop the daemon's tick over a diagnosis."""
    path = tmp_path / ".control" / ATTEMPTS_FILE
    path.parent.mkdir(parents=True)
    path.write_text('{"tier": "offbox", "outcome": "complete", "elapsed_s": 10}\n'
                    '{"tier": "offbox", "outcome": "fail\n', encoding="utf-8")
    row = newest_attempt("offbox", tmp_path)
    assert row is not None
    assert row["outcome"] == "complete"


def test_no_record_at_all_is_no_record_and_not_an_error(tmp_path):
    """The record does not exist until backup.sh has run once under the new version, and a
    verdict composed before then must read exactly as it did before."""
    assert newest_attempt("offbox", tmp_path) is None
    assert attempt_detail("off-box", tmp_path) == ""


def test_the_clause_names_the_outcome_the_seconds_and_what_moved(tmp_path):
    """The whole point of D299: the numbers a person needs to tell "missed by a minute" from
    "missed by three hours" are IN the sentence, not in a journal they must go and open."""
    _write(tmp_path, [{"at": "2026-11-23T01:00:00+01:00", "tier": "offbox",
                       "outcome": "failed", "elapsed_s": 14400, "note": "rsync exit 30"}])
    clause = attempt_detail("off-box", tmp_path)
    assert "failed" in clause
    assert "14400s" in clause
    assert "rsync exit 30" in clause
    assert ATTEMPTS_FILE in clause


def test_a_completed_attempt_says_what_it_transferred(tmp_path):
    """rsync's own figure, which backup.sh already computes for its stdout — the measure of
    whether a night was slow because it had a lot to move or slow for its own reasons."""
    _write(tmp_path, [{"at": "2026-11-23T04:09:11+01:00", "tier": "offbox",
                       "outcome": "complete", "elapsed_s": 11000, "transferred": "1.70G"}])
    assert "moving 1.70G" in attempt_detail("off-box", tmp_path)


def test_a_stale_verdict_carries_the_cost_and_an_ok_one_does_not(tmp_path, monkeypatch):
    """An `ok` verdict has nothing to diagnose and `unknown` means the stamps were unreadable, so
    the clause belongs to the stale verdict alone — otherwise every healthy night's event would
    carry a backup post-mortem nobody asked for."""
    local = tmp_path / "local"
    (local).mkdir()
    old = datetime.now(UTC) - timedelta(days=9)
    (local / ".rsched-backup-completed").write_text(old.isoformat(), encoding="utf-8")
    _write(tmp_path, [{"at": "2026-11-23T01:00:00+01:00", "tier": "local",
                       "outcome": "failed", "elapsed_s": 14400}])
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [local, tmp_path / "gone"])
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path.parent))
    monkeypatch.setenv("HOME", str(tmp_path.parent))
    # the routines home this reader defaults to is <home>/routines; point it at our fixture
    (tmp_path.parent / "routines").mkdir(exist_ok=True)
    (tmp_path.parent / "routines" / ".control").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "routines" / ".control" / ATTEMPTS_FILE).write_text(
        json.dumps({"tier": "local", "outcome": "failed", "elapsed_s": 14400}) + "\n",
        encoding="utf-8")
    state, detail = backup_watch.staleness()
    assert state == "stale"
    assert "the last recorded attempt was failed after 14400s" in detail

    # The ok case needs a RECORDED attempt of its own tier, or "no clause" would hold simply
    # because there was nothing to say — the assertion has to fail when the condition is dropped.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / ".rsched-backup-completed").write_text(
        datetime.now(UTC).isoformat(), encoding="utf-8")
    (tmp_path.parent / "routines" / ".control" / ATTEMPTS_FILE).write_text(
        json.dumps({"tier": "local", "outcome": "complete", "elapsed_s": 62}) + "\n",
        encoding="utf-8")
    fresh2 = tmp_path / "fresh2"
    fresh2.mkdir()
    (fresh2 / ".rsched-backup-completed").write_text(
        datetime.now(UTC).isoformat(), encoding="utf-8")
    # BOTH tiers fresh, so the verdict is `ok` while the two-tier path that composes the clause
    # still runs — a single root skips that branch entirely and would prove nothing.
    monkeypatch.setattr(backup_watch, "backup_roots", lambda env=None: [fresh, fresh2])
    ok_state, ok_detail = backup_watch.staleness()
    assert ok_state == "ok"
    assert "last recorded attempt" not in ok_detail


def test_the_stale_remedy_names_the_readable_record_before_the_journal():
    """The card D299 came from said no run could diagnose this, because the remedy it printed was
    `journalctl --user -u rsched-backup` — which does not exist inside a run's sandbox. The
    readable path comes FIRST in the sentence now; the journal stays for whoever has a shell."""
    source = (REPO / "src" / "rsched" / "daemon" / "backup_watch.py").read_text(encoding="utf-8")
    journal = source.index("journalctl --user -u rsched-backup")
    remedy = source[source.index("ATTEMPT_BOUND_HOURS}h, and {last}"):journal]
    assert "ATTEMPTS_FILE" in remedy, (
        "the stale verdict must name the readable record BEFORE the journal, so a reader without "
        "a shell is told where to look first")


@pytest.mark.parametrize("call", [
    'record_attempt local complete "${LOCAL_STARTED_AT}"',
    'record_attempt offbox "failed" "${started_at}" "" "rsync exit ${rc}"',
    'record_attempt offbox complete "${started_at}" "${sent:-}"',
])
def test_backup_sh_records_every_attempt_outcome(call):
    """Three outcomes are worth recording and all three are wired: the local tier completing, the
    off-box tier completing with its transferred figure, and the off-box tier FAILING — which is
    the one that happens nightly on this instance and the reason D299 exists."""
    assert call in BACKUP_SH.read_text(encoding="utf-8")


def test_backup_sh_is_still_valid_shell():
    """This change adds a function and a quoted heredoc to a 758-line script that runs unattended
    at night as the only thing standing between this instance and a disk loss."""
    done = subprocess.run(["bash", "-n", str(BACKUP_SH)], capture_output=True,
                          text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_the_recorder_never_fails_the_backup():
    """A backup must not fail because its own bookkeeping could not be written: the record is a
    diagnosis, not the protection. Both the directory creation and the python call swallow their
    own failure."""
    source = BACKUP_SH.read_text(encoding="utf-8")
    body = source[source.index("record_attempt() {"):source.index("LOCAL_STARTED_AT=")]
    assert "|| return 0" in body
    assert "|| true" in body


def test_the_record_lives_where_a_run_can_read_it(tmp_path):
    """`.control/` under the routines home is the directory every run already reads (the health
    event log is there). A path on the backup share itself would be unreadable from a run's jail,
    which is the whole defect this fixes."""
    assert attempts_path(tmp_path) == tmp_path / ".control" / ATTEMPTS_FILE
    assert "routines/.control/backup-attempts.jsonl" in BACKUP_SH.read_text(encoding="utf-8")
