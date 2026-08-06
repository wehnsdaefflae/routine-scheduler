"""D78-A pending-edit spool: queue validation + replay robustness."""

import pytest

from rsched import pending_edits


def test_queue_rejects_unknown_kind(tmp_path):
    """Fail closed: a kind with no applier can never enter the spool."""
    with pytest.raises(ValueError):
        pending_edits.queue(tmp_path, "r", "bogus_kind", {})
    assert pending_edits.pending_count(tmp_path, "r") == 0


def test_replay_records_failure_and_drops_file(tmp_path):
    """A single bad edit is surfaced (ok=False), its file dropped, and the rest still
    apply — one malformed edit must never wedge the whole queue."""
    home = tmp_path
    rdir = tmp_path / "routines" / "r"
    (rdir / "stages").mkdir(parents=True)
    (rdir / "routine.yaml").write_text("enabled: true\n", encoding="utf-8")

    # a file edit missing its required `path` key (KeyError in the applier) …
    pending_edits.queue(home, "r", "file", {"content": "no path"})
    # … followed by a valid file edit.
    pending_edits.queue(home, "r", "file", {"path": "stages/ok.md", "content": "good"})
    assert pending_edits.pending_count(home, "r") == 2

    rows = pending_edits.apply_pending(rdir, home, "r")
    assert len(rows) == 2
    assert rows[0]["ok"] is False and "KeyError" in rows[0]["error"]
    assert rows[1]["ok"] is True
    # both files consumed regardless of the failure; the good edit landed
    assert pending_edits.pending_count(home, "r") == 0
    assert (rdir / "stages" / "ok.md").read_text() == "good"


def test_replay_empty_spool_is_noop(tmp_path):
    assert pending_edits.apply_pending(tmp_path / "r", tmp_path, "r") == []
