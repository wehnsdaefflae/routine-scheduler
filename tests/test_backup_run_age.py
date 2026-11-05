"""`deploy/backup.sh` stops re-copying OLD run directories, and keeps everything else whole.

D235, the operator's answer to F830 verbatim: *"Exclude runs/ older than N days from the snapshot
(keep state/, .memory/, LEDGER, audit/) — biggest cut, loses old transcripts off-box"*. The
snapshot had stopped completing inside the unit's `TimeoutStartSec=4h` — no complete one between
2026-11-01 and 2026-11-05, three `backup_stale` nights in a row — and the share is sshfs, one
round trip per file, so the file COUNT is the cost.

Each property here is one the shell can break silently, and two of them lose data if they break:

- a run directory older than `KEEP_RUNS_DAYS` is not carried; a recent one is;
- everything his answer names is carried AT ANY AGE — `state/`, `.memory/`, `LEDGER.md`,
  `audit/` — and so is a `conversations/` run folder, which is a session and not a transcript;
- a directory whose name is not a run id is KEPT: "unparseable" must never be read as "old";
- `KEEP_RUNS_DAYS=0` carries every run directory, so the cut is switchable from the unit file;
- the MIGRATION TARBALL still carries the aged-out run, because `deploy/bundle.sh`'s source host
  is decommissioned immediately afterwards and that copy has to be complete. This is the one
  deliberate difference between the two consumers of `deploy/state-paths.sh`, which is why the age
  cut is in `backup.sh` and not in the shared `STATE_EXCLUDES`
  (`tests/test_backup_snapshots.py::test_the_tarball_and_the_snapshot_carry_the_same_files` is
  the test that would otherwise have forbidden it).

The cutoff is computed by the real `date -u -d "N days ago"` — `_backup`'s date stub answers only
`+%F` — so every fixture here is planted against the REAL UTC clock, not the stubbed snapshot day.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_backup_snapshots import _backup, _ok, share_root
from test_deploy_state import _bundle, _home, _tar_files

#: The stubbed day a snapshot is NAMED after. Unrelated to the age cutoff, which the real clock
#: answers — so the two can never be confused by a reader of this file.
SNAPSHOT_DAY = "2026-10-01"


def _mk_run_id(days_ago: int) -> str:
    """A run id as the engine writes it: `<YYYYMMDD>-<HHMMSS>`, always UTC."""
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return f"{stamp:%Y%m%d}-{stamp:%H%M%S}"


#: Each age's run id, computed ONCE at import — the fixture plants these names and the assertions
#: look them up, so the two can never disagree. Computing one per call made a test fail whenever
#: the clock crossed a second between planting and asserting (it did, on the first red-first run).
RUN_IDS = {days: _mk_run_id(days) for days in (30, 8, 2, 0)}


def _run_id(days_ago: int) -> str:
    return RUN_IDS[days_ago]


@pytest.fixture
def home(tmp_path) -> Path:
    """The standard instance, plus one routine with run history at four ages and a full set of
    the things the operator's answer keeps."""
    home = _home(tmp_path)
    r = home / "routines/aged"
    for days, name in ((30, "old"), (8, "just-old"), (2, "recent"), (0, "today")):
        d = r / "runs" / _run_id(days)
        d.mkdir(parents=True)
        (d / "transcript.jsonl").write_text(f"{name} run\n", encoding="utf-8")
        (d / "sub/1").mkdir(parents=True)
        (d / "sub/1/status.json").write_text("{}", encoding="utf-8")
    # Not a run id: a folder someone left in runs/, which must be carried whatever its mtime.
    (r / "runs/scratch").mkdir(parents=True)
    (r / "runs/scratch/notes.txt").write_text("not a run id", encoding="utf-8")
    for rel, text in (("state/plan.md", "the plan"), (".memory/INDEX.md", "the index"),
                      ("LEDGER.md", "the ledger"), ("audit/report.json", "{}"),
                      ("questions/pending/q.json", "{}"), ("artifacts/out.pdf", "%PDF")):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text, encoding="utf-8")
    # A conversation is routine-shaped and may hold run folders: carried whole, at any age.
    conv = home / "conversations/c1/runs" / _run_id(30)
    conv.mkdir(parents=True)
    (conv / "transcript.jsonl").write_text("a session he had\n", encoding="utf-8")
    return home


@pytest.fixture
def root(tmp_path) -> Iterator[Path]:
    with share_root(tmp_path) as built:
        yield built


def _copied(root: Path) -> set[str]:
    snap = root / "snapshots" / SNAPSHOT_DAY
    return {str(p.relative_to(snap)) for p in snap.rglob("*") if p.is_symlink() or p.is_file()}


def _run_dirs(copied: set[str], prefix: str = "routines/aged/runs/") -> set[str]:
    return {p[len(prefix):].split("/")[0] for p in copied if p.startswith(prefix)}


def test_a_run_older_than_the_window_is_not_carried_and_a_recent_one_is(home, root):
    """The cut itself, at the default seven days: the 8- and 30-day runs are gone, the 2-day and
    today's are whole (their `sub/` included — excluding the directory excludes the tree)."""
    proc = _backup(home, root, SNAPSHOT_DAY)
    _ok(proc)
    carried = _run_dirs(_copied(root))
    assert _run_id(30) not in carried, carried
    assert _run_id(8) not in carried, carried
    assert _run_id(2) in carried, carried
    assert _run_id(0) in carried, carried
    assert f"routines/aged/runs/{_run_id(2)}/sub/1/status.json" in _copied(root)
    assert "2 run directories older than 7 days are not" in proc.stdout, proc.stdout


def test_everything_his_answer_keeps_is_carried_at_any_age(home, root):
    """`state/`, `.memory/`, `LEDGER`, `audit/` — his words — plus the two things a reader would
    worry about next: a routine's questions/ and artifacts/, and a conversation's own run folder,
    which is a session and not a run transcript."""
    _ok(_backup(home, root, SNAPSHOT_DAY))
    copied = _copied(root)
    for rel in ("routines/aged/state/plan.md", "routines/aged/.memory/INDEX.md",
                "routines/aged/LEDGER.md", "routines/aged/audit/report.json",
                "routines/aged/questions/pending/q.json", "routines/aged/artifacts/out.pdf",
                f"conversations/c1/runs/{_run_id(30)}/transcript.jsonl"):
        assert rel in copied, f"{rel} was dropped; snapshot holds {sorted(copied)}"


def test_a_directory_that_is_not_a_run_id_is_kept(home, root):
    """The direction that loses data if it is wrong: an unparseable name read as "old"."""
    _ok(_backup(home, root, SNAPSHOT_DAY))
    assert "routines/aged/runs/scratch/notes.txt" in _copied(root)


def test_zero_carries_every_run_directory(home, root):
    """So the cut is switchable from the unit file without editing the script."""
    proc = _backup(home, root, SNAPSHOT_DAY, overrides={"KEEP_RUNS_DAYS": "0"})
    _ok(proc)
    carried = _run_dirs(_copied(root))
    assert {_run_id(30), _run_id(8), _run_id(2), _run_id(0)} <= carried, carried
    assert "every run directory is carried" in proc.stdout, proc.stdout


def test_a_resumed_run_drops_what_an_interrupted_one_had_already_copied(home, root):
    """A night cut short leaves a half-built folder the next run CARRIES ON in, so the aged-out
    run can already be sitting in it — `--delete-excluded` is what makes a resumed night converge
    on the same snapshot as a fresh one."""
    stale = root / "snapshots/.in-progress/routines/aged/runs" / _run_id(30)
    stale.mkdir(parents=True)
    (stale / "transcript.jsonl").write_text("copied before the cut existed", encoding="utf-8")
    _ok(_backup(home, root, SNAPSHOT_DAY))
    assert _run_id(30) not in _run_dirs(_copied(root))


def test_the_migration_tarball_still_carries_every_run(home, tmp_path):
    """The deliberate difference, and the reason the cut is NOT in the shared STATE_EXCLUDES:
    `bundle.sh` writes the one-shot tarball for a host move whose source is decommissioned
    immediately afterwards, so that copy must be complete at every age."""
    out = tmp_path / "bundle.tgz"
    proc = _bundle(home, out)
    assert proc.returncode == 0, proc.stderr
    tarred = _tar_files(out)
    for days in (30, 8, 2, 0):
        rel = f"routines/aged/runs/{_run_id(days)}/transcript.jsonl"
        assert rel in tarred, f"the migration tarball dropped {rel}"
