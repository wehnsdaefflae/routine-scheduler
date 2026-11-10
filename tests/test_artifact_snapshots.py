"""⟲ rewind puts a conversation's FILES back, not only its transcript.

A scheduled routine's dir is a git repo the engine autocommits at every run end, so its
deliverables have a history. A CONVERSATION is not versioned: `artifacts/` holds exactly what
the newest reply left there. So a rewind used to take the transcript back to a chosen turn and
leave the files as the discarded replies wrote them — the conversation saying one thing and its
deliverables another, which is the error nobody notices until they act on the wrong file.

What is pinned here: a reply's close-out snapshots `artifacts/`, a rewind restores the newest
snapshot at or before the turn it kept, what was live is copied aside first (a rewind is itself
an act someone may regret), the store is bounded, and a conversation that produced no files pays
nothing at all.
"""

from __future__ import annotations

from pathlib import Path

from rsched.engine import artsnap
from rsched.engine.rewind import rewind_transcript


def _conv(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "c-x"
    (d / "artifacts").mkdir(parents=True)
    for name, text in files.items():
        (d / "artifacts" / name).write_text(text, encoding="utf-8")
    return d


def test_a_reply_that_wrote_files_leaves_a_snapshot_named_for_its_turn(tmp_path):
    d = _conv(tmp_path, {"report.md": "first draft"})

    snap = artsnap.take(d, "20261110-140003", 3)

    assert snap is not None and snap.name == "20261110-140003-t3"
    assert (snap / "report.md").read_text() == "first draft"
    # dotted, so the console's artifact listing never serves a snapshot as a deliverable
    assert snap.parent.name.startswith(".")


def test_a_reply_that_wrote_nothing_costs_no_snapshot(tmp_path):
    """The common case. An empty directory copied per reply would be pure noise in the store
    and would push a real snapshot out of the bound."""
    d = tmp_path / "c-y"
    (d / "artifacts").mkdir(parents=True)

    assert artsnap.take(d, "20261110-140003", 1) is None
    assert not (d / artsnap.SNAP_DIR).exists()


def test_the_store_is_bounded_and_prunes_the_oldest(tmp_path):
    """A conversation with 200 replies and a 40 MB artefact set would otherwise grow by 8 GB,
    silently. The bound is per conversation and keeps the NEWEST, which is what a rewind
    reaches for."""
    d = _conv(tmp_path, {"a.txt": "x"})
    for turn in range(1, artsnap.KEEP + 4):
        (d / "artifacts" / "a.txt").write_text(f"turn {turn}", encoding="utf-8")
        artsnap.take(d, "20261110-140003", turn)

    kept = sorted(p.name for p in (d / artsnap.SNAP_DIR).iterdir() if p.is_dir())
    assert len(kept) == artsnap.KEEP, kept
    # the newest survived and the oldest went
    assert f"20261110-140003-t{artsnap.KEEP + 3}" in kept
    assert "20261110-140003-t1" not in kept


def test_restore_takes_the_newest_snapshot_at_or_before_the_turn(tmp_path):
    """Not simply the newest: a rewind to turn 3 must not restore what turn 7 wrote."""
    d = _conv(tmp_path, {"report.md": "at turn 1"})
    artsnap.take(d, "20261110-140003", 1)
    (d / "artifacts" / "report.md").write_text("at turn 3", encoding="utf-8")
    artsnap.take(d, "20261110-140003", 3)
    (d / "artifacts" / "report.md").write_text("at turn 7", encoding="utf-8")
    artsnap.take(d, "20261110-140003", 7)

    out = artsnap.restore(d, 3)

    assert out is not None and out["from"] == "20261110-140003-t3"
    assert (d / "artifacts" / "report.md").read_text() == "at turn 3"


def test_restore_keeps_what_was_live_before_it_overwrote_it(tmp_path):
    """A rewind is itself an act someone may regret, and the files a discarded reply wrote may
    be the ones that mattered. They are copied aside under a name a person can find."""
    d = _conv(tmp_path, {"report.md": "kept state"})
    artsnap.take(d, "20261110-140003", 1)
    (d / "artifacts" / "report.md").write_text("the state being discarded", encoding="utf-8")

    out = artsnap.restore(d, 1)

    assert out is not None and out["kept"] == "pre-restore-20261110-140003-t1"
    backup = d / artsnap.SNAP_DIR / out["kept"] / "report.md"
    assert backup.read_text() == "the state being discarded"


def test_a_file_a_later_reply_added_is_gone_after_the_restore(tmp_path):
    """The half a copy-over would miss: restoring must leave `artifacts/` as the snapshot had
    it, not merged with what came after. A stray deliverable from a discarded reply is exactly
    the file someone would act on."""
    d = _conv(tmp_path, {"report.md": "at turn 1"})
    artsnap.take(d, "20261110-140003", 1)
    (d / "artifacts" / "extra.md").write_text("written by the reply being discarded",
                                              encoding="utf-8")

    artsnap.restore(d, 1)

    assert (d / "artifacts" / "report.md").exists()
    assert not (d / "artifacts" / "extra.md").exists()


def test_restoring_past_every_snapshot_claims_nothing(tmp_path):
    """None is not a failure — a conversation may have produced no files, or its history may
    be pruned past that turn. A caller that read None as success would report a restore that
    did not happen."""
    d = _conv(tmp_path, {"report.md": "at turn 5"})
    artsnap.take(d, "20261110-140003", 5)

    assert artsnap.restore(d, 2) is None
    assert artsnap.restore(tmp_path / "c-none", 9) is None


def test_a_rewind_restores_the_files_and_reports_what_it_put_back(tmp_path):
    """The two halves together, through the real `rewind_transcript`: the transcript goes back
    to the kept turn and `artifacts/` goes back with it.
    """
    d = _conv(tmp_path, {"report.md": "at turn 1"})
    run_dir = d / "runs" / "20261110-140003"
    run_dir.mkdir(parents=True)
    events = [
        '{"type": "header", "run_id": "c-x:20261110-140003", "routine": "c-x"}',
        '{"type": "assistant_action", "turn": 1, "payload": {"kind": "write_file"}}',
        '{"type": "observation", "turn": 1, "payload": {"kind": "write_file"}}',
        '{"type": "assistant_action", "turn": 2, "payload": {"kind": "write_file"}}',
        '{"type": "observation", "turn": 2, "payload": {"kind": "write_file"}}',
    ]
    (run_dir / "transcript.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")
    artsnap.take(d, "20261110-140003", 1)
    (d / "artifacts" / "report.md").write_text("at turn 2", encoding="utf-8")
    artsnap.take(d, "20261110-140003", 2)

    info = rewind_transcript(run_dir, 1)

    assert info is not None and info["kept_through_turn"] == 1
    assert info["artifacts"]["from"] == "20261110-140003-t1"
    assert (d / "artifacts" / "report.md").read_text() == "at turn 1"


def test_a_rewind_with_no_snapshots_reports_no_artifact_restore(tmp_path):
    """A routine run reaches the same code and has no snapshot store at all (its dir is the git
    repo the autocommit writes). The key is ABSENT rather than false-y, so a reader cannot
    mistake "nothing to put back" for "the files were restored".
    """
    d = tmp_path / "c-z"
    run_dir = d / "runs" / "20261110-140003"
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        '{"type": "header", "run_id": "c-z:20261110-140003"}\n'
        '{"type": "assistant_action", "turn": 1, "payload": {"kind": "read_file"}}\n'
        '{"type": "observation", "turn": 1, "payload": {"kind": "read_file"}}\n'
        '{"type": "assistant_action", "turn": 2, "payload": {"kind": "read_file"}}\n'
        '{"type": "observation", "turn": 2, "payload": {"kind": "read_file"}}\n', encoding="utf-8")

    info = rewind_transcript(run_dir, 1)

    assert info is not None and "artifacts" not in info
