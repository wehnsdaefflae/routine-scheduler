"""fileactivity: the per-file read/write read-model over a run's transcript."""

import json

from rsched.fileactivity import file_activity


def _write_transcript(d, events):
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_file_activity_aggregates_per_path(tmp_path):
    """Reads (single + batched, view_image too), writes, and edits aggregate per path in
    first-touched order; a failed touch counts as an error, not an op; bytes total the
    successful write_file payloads; user slash commands (observation payloads with a
    user_command flag) count like any other touch."""
    _write_transcript(tmp_path, [
        {"type": "header", "payload": {}},
        {"type": "assistant_action", "turn": 1, "payload": {"kind": "read_file"}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "read_file", "path": "state/notes.md", "content": "x"}},
        {"type": "observation", "turn": 2,
         "payload": {"kind": "read_file", "files": [
             {"path": "state/notes.md", "content": "x"},
             {"path": "missing.md", "error": "no such file"}]}},
        {"type": "observation", "turn": 3,
         "payload": {"kind": "write_file", "path": "artifacts/report.html", "bytes": 120}},
        {"type": "observation", "turn": 4,
         "payload": {"kind": "edit_file", "path": "artifacts/report.html", "replacements": 1}},
        {"type": "observation", "turn": 5,
         "payload": {"kind": "view_image", "files": [{"path": "shot.png"}]}},
        # a user slash command's observation — no model turn, same payload shape
        {"type": "observation",
         "payload": {"kind": "write_file", "path": "artifacts/report.html", "bytes": 30,
                     "user_command": True}},
        # a failed write is an error, not a write
        {"type": "observation", "turn": 6,
         "payload": {"kind": "write_file", "path": "routine.yaml",
                     "error": "routine.yaml is never writable"}},
        # non-file kinds contribute nothing
        {"type": "observation", "turn": 7, "payload": {"kind": "util", "name": "gu", "exit": 0}},
    ])
    assert file_activity(tmp_path) == [
        {"path": "state/notes.md", "reads": 2, "writes": 0, "edits": 0, "bytes": 0,
         "errors": 0, "sub": False},
        {"path": "missing.md", "reads": 0, "writes": 0, "edits": 0, "bytes": 0,
         "errors": 1, "sub": False},
        {"path": "artifacts/report.html", "reads": 0, "writes": 2, "edits": 1, "bytes": 150,
         "errors": 0, "sub": False},
        {"path": "shot.png", "reads": 1, "writes": 0, "edits": 0, "bytes": 0,
         "errors": 0, "sub": False},
        {"path": "routine.yaml", "reads": 0, "writes": 0, "edits": 0, "bytes": 0,
         "errors": 1, "sub": False},
    ]


def test_file_activity_includes_children(tmp_path):
    """A subtask's touches fold into the parent's rows (recursively), flagged `sub` —
    a path the parent also touched keeps one row."""
    _write_transcript(tmp_path, [
        {"type": "observation", "turn": 1,
         "payload": {"kind": "write_file", "path": "state/shared.md", "bytes": 10}},
    ])
    _write_transcript(tmp_path / "sub" / "1", [
        {"type": "observation", "turn": 1,
         "payload": {"kind": "write_file", "path": "state/shared.md", "bytes": 5}},
        {"type": "observation", "turn": 2,
         "payload": {"kind": "read_file", "path": "docs/child-only.md", "content": "y"}},
    ])
    _write_transcript(tmp_path / "sub" / "1" / "sub" / "1", [
        {"type": "observation", "turn": 1,
         "payload": {"kind": "edit_file", "path": "deep.md", "replacements": 2}},
    ])
    assert file_activity(tmp_path) == [
        {"path": "state/shared.md", "reads": 0, "writes": 2, "edits": 0, "bytes": 15,
         "errors": 0, "sub": True},
        {"path": "docs/child-only.md", "reads": 1, "writes": 0, "edits": 0, "bytes": 0,
         "errors": 0, "sub": True},
        {"path": "deep.md", "reads": 0, "writes": 0, "edits": 1, "bytes": 0,
         "errors": 0, "sub": True},
    ]


def test_file_activity_empty_without_transcript(tmp_path):
    assert file_activity(tmp_path) == []
