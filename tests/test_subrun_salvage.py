"""F732: a RESUMED run must not lose the children an earlier leg started.

The live registry (`SubrunManager.subruns`) is in-memory, so a resumed leg builds a fresh,
empty one while `runs/<ts>/sub/<n>/` still holds every child directory — one with a finished
hand-back in `artifacts/`. The operator asked for SALVAGE, not recovery (2026-10-19): the
work on disk must be visible and named, and the reassuring sentence "no sub-workflows
spawned this run" must never be printed over a tree that holds children.

What is pinned here:
  * a child directory on disk becomes a row, marked `orphaned-by-resume`, carrying its dir;
  * a LIVE child's row is unchanged, and a child both live and on disk is reported ONCE
    (the live row wins — it has the thread, the turn count and the summary);
  * the empty-case prose distinguishes "no record in THIS leg" from "none ever spawned";
  * `wait` and `kill` REFUSE an orphaned n with prose that says why it cannot be addressed,
    rather than the generic "no sub-workflow N" that reads as "it never existed".
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from rsched.engine import obs_children, subruns


def _ctx(run_dir):
    return SimpleNamespace(run_dir=run_dir, root_run_dir=run_dir, turn=0)


def _mgr(run_dir):
    return subruns.SubrunManager(SimpleNamespace(ctx=_ctx(run_dir)))


def _child_dir(run_dir, n, *, label="worker", workflow="general-task", artifacts=()):
    d = run_dir / "sub" / str(n)
    d.mkdir(parents=True)
    (d / "instruction.md").write_text(f"brief for {label}\n")
    (d / "transcript.jsonl").write_text(
        json.dumps({"type": "run_started", "label": label, "workflow": workflow}) + "\n")
    if artifacts:
        (d / "artifacts").mkdir()
        for name in artifacts:
            (d / "artifacts" / name).write_text("handed back\n")
    return d


# -- the salvage itself ----------------------------------------------------------------


def test_a_resumed_leg_sees_the_children_on_disk(tmp_path):
    """The defect: four children spawned, the run resumed, and `subruns` answered
    'no sub-workflows spawned this run' over seven directories on disk."""
    for n in (1, 2, 3):
        _child_dir(tmp_path, n)
    table = _mgr(tmp_path).status_table()
    assert table["count"] == 3
    assert [r["n"] for r in table["rows"]] == [1, 2, 3]
    assert all(r["state"] == "orphaned-by-resume" for r in table["rows"])


def test_an_orphaned_row_names_its_directory(tmp_path):
    """Salvage means the parent can GO AND LOOK: the row carries the path."""
    _child_dir(tmp_path, 1, artifacts=("report.md",))
    row = _mgr(tmp_path).status_table()["rows"][0]
    assert row["dir"].endswith("sub/1")
    assert row["artifacts"] == ["report.md"]


def test_a_live_child_is_not_duplicated_by_its_own_directory(tmp_path):
    """A running child HAS a directory; it must be reported once, from the live registry."""
    _child_dir(tmp_path, 1)
    mgr = _mgr(tmp_path)
    mgr.subruns[1] = SimpleNamespace(
        n=1, label="live", workflow="general-task", mode="parallel",
        done=threading.Event(), status="running", summary="",
        ctx=SimpleNamespace(turn=7), started_mono=0.0, ended_mono=3.0)
    rows = mgr.status_table()["rows"]
    assert len(rows) == 1
    assert rows[0]["label"] == "live"
    assert rows[0]["turns"] == 7
    # `dir`/`artifacts` are orphan-only: a live child's hand-back arrives by itself.
    assert "dir" not in rows[0]


def test_live_and_orphaned_children_are_ordered_by_number(tmp_path):
    for n in (1, 2):
        _child_dir(tmp_path, n)
    mgr = _mgr(tmp_path)
    mgr.subruns[3] = SimpleNamespace(
        n=3, label="live", workflow="general-task", mode="parallel",
        done=threading.Event(), status="running", summary="",
        ctx=SimpleNamespace(turn=1), started_mono=0.0, ended_mono=1.0)
    assert [r["n"] for r in mgr.status_table()["rows"]] == [1, 2, 3]


def test_no_directory_and_no_live_child_is_the_genuine_empty_case(tmp_path):
    table = _mgr(tmp_path).status_table()
    assert table["count"] == 0
    assert table["rows"] == []


# -- the prose a run actually reads ---------------------------------------------------


def test_the_empty_sentence_no_longer_claims_none_were_spawned(tmp_path):
    """'no sub-workflows spawned this run' is a CLAIM about history that a fresh leg cannot
    make. The replacement scopes itself to this leg and names the reason."""
    text = obs_children.format_children({"kind": "subruns", "count": 0, "rows": []}, "subruns")
    assert "no sub-workflows spawned this run" not in text
    assert "this leg" in text.lower()


def test_an_orphaned_row_renders_its_directory(tmp_path):
    text = obs_children.format_children({"kind": "subruns", "count": 1, "rows": [
        {"n": 2, "label": "worker", "workflow": "general-task", "mode": "parallel",
         "state": "orphaned-by-resume", "turns": 0, "elapsed_s": 0.0, "summary_head": "",
         "dir": "/runs/x/sub/2", "artifacts": ["report.md"]}]}, "subruns")
    assert "orphaned-by-resume" in text
    assert "/runs/x/sub/2" in text
    assert "report.md" in text


# -- addressing an orphan ------------------------------------------------------------


def test_wait_refuses_an_orphaned_child_with_the_reason(tmp_path):
    _child_dir(tmp_path, 1)
    err = _mgr(tmp_path).wait({"n": 1}, poll_s=0.01, aborted=lambda: False)["error"]
    assert "EARLIER LEG" in err
    assert "sub/1" in err


def test_kill_refuses_an_orphaned_child_with_the_reason(tmp_path):
    _child_dir(tmp_path, 1)
    err = _mgr(tmp_path).kill(1)["error"]
    assert "EARLIER LEG" in err


def test_wait_with_no_children_at_all_keeps_its_plain_error(tmp_path):
    err = _mgr(tmp_path).wait({}, poll_s=0.01, aborted=lambda: False)["error"]
    assert "no sub-workflows" in err
