"""D241's last rename, on the transcripts already on disk (`migrate_child_events`) — the
rename is only half a change, because three readers dispatch on the event `type` and every one
of them fails SILENTLY on a token it does not know, so a stored transcript left on the old
names loses rows from the console, empties the child-run tree and understates a resumed leg;
these tests pin the properties that make rewriting a RECORD defensible — only the `type` field,
only where it is one of the two names, prose untouched, and exactly once.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from rsched.migrate_child_events import RECORD, run_migration

OLD_ROWS = [
    {"ts": "2026-11-01T10:00:00+01:00", "type": "child_start_decoy", "payload": {"n": 1}},
    {"ts": "2026-11-01T10:00:01+01:00", "type": "subrun_start",
     "payload": {"n": 1, "label": "seq1", "mode": "sequential"}},
    {"ts": "2026-11-01T10:05:00+01:00", "type": "subrun_end",
     "payload": {"n": 1, "status": "ok", "turns": 14}},
    # prose that MENTIONS the old name: a say, and an observation quoting an event
    {"ts": "2026-11-01T10:06:00+01:00", "type": "assistant_action",
     "payload": {"say": "the subrun_end event is what I am reading", "kind": "read_file"}},
]


def _server(tmp_path: Path) -> SimpleNamespace:
    for name in ("routines", "conversations", "background"):
        (tmp_path / name).mkdir()
    return SimpleNamespace(routines_home=tmp_path / "routines",
                           conversations_home=tmp_path / "conversations",
                           background_home=tmp_path / "background")


def _write(path: Path, rows: list[dict], *, gz: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_bytes(gzip.compress(body.encode()) if gz else body.encode())


def _read(path: Path) -> list[dict]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


def test_a_stored_transcript_carries_the_new_event_names(tmp_path):
    server = _server(tmp_path)
    t = server.routines_home / "slug" / "runs" / "20261101-100000" / "transcript.jsonl"
    _write(t, OLD_ROWS)

    record = run_migration(server)

    types = [r["type"] for r in _read(t)]
    assert types == ["child_start_decoy", "child_start", "child_end", "assistant_action"], types
    assert record["events_renamed"] == 2
    assert record["files_changed"] == 1


def test_prose_naming_the_old_event_survives_the_rewrite(tmp_path):
    """The rewrite is a key assignment on a parsed row and never a text substitution, so a
    say, an observation or a summary that quotes `subrun_end` — a statement about the past that
    stays true — survives untouched while only the dispatch token moves."""
    server = _server(tmp_path)
    t = server.routines_home / "slug" / "runs" / "20261101-100000" / "transcript.jsonl"
    _write(t, OLD_ROWS)

    run_migration(server)

    said = next(r for r in _read(t) if r["type"] == "assistant_action")
    assert said["payload"]["say"] == "the subrun_end event is what I am reading"


def test_a_gzipped_archive_is_migrated_and_stays_gzipped(tmp_path):
    """Most of this instance's transcripts are retention-gzipped; skipping them would leave the
    majority of run pages rendering less than they did before the rename."""
    server = _server(tmp_path)
    t = server.conversations_home / "c1" / "runs" / "20261101-100000" / "transcript.jsonl.gz"
    _write(t, OLD_ROWS, gz=True)

    run_migration(server)

    assert t.read_bytes()[:2] == b"\x1f\x8b", "the archive must stay gzipped"
    assert [r["type"] for r in _read(t)][1:3] == ["child_start", "child_end"]


def test_a_child_s_own_transcript_deep_in_the_run_tree_is_migrated(tmp_path):
    """A child records its own children, so the walk is the whole tree and not `runs/*/`."""
    server = _server(tmp_path)
    t = (server.routines_home / "slug" / "runs" / "20261101-100000"
         / "sub" / "2" / "sub" / "1" / "transcript.jsonl")
    _write(t, OLD_ROWS)

    run_migration(server)

    assert [r["type"] for r in _read(t)][1:3] == ["child_start", "child_end"]


def test_a_malformed_line_is_copied_through_untouched(tmp_path):
    """A truncated last line is the normal shape of a transcript whose run was killed: the
    migration must not be the thing that discards it."""
    server = _server(tmp_path)
    t = server.routines_home / "slug" / "runs" / "20261101-100000" / "transcript.jsonl"
    _write(t, OLD_ROWS)
    with t.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-11-01T10:07:00+01:00", "type": "obser')

    run_migration(server)

    assert t.read_text(encoding="utf-8").endswith('{"ts": "2026-11-01T10:07:00+01:00", '
                                                  '"type": "obser')


def test_it_runs_once_and_its_record_says_what_it_did(tmp_path):
    """Its own record is the off switch — a second boot must not re-read 330 MiB of JSONL."""
    server = _server(tmp_path)
    t = server.routines_home / "slug" / "runs" / "20261101-100000" / "transcript.jsonl"
    _write(t, OLD_ROWS)

    first = run_migration(server)
    assert (Path(server.routines_home) / RECORD).is_file()
    assert first["renames"] == {"subrun_start": "child_start", "subrun_end": "child_end"}

    _write(t, OLD_ROWS)                      # the old names put back by hand
    assert run_migration(server) is None
    assert [r["type"] for r in _read(t)][1] == "subrun_start", "the second pass must do nothing"


def test_a_transcript_without_the_old_names_is_not_rewritten(tmp_path):
    """The pre-filter is what keeps the boot cost bounded; prove it leaves bytes alone."""
    server = _server(tmp_path)
    t = server.background_home / "b1" / "runs" / "20261101-100000" / "transcript.jsonl"
    rows = [{"ts": "2026-11-01T10:00:00+01:00", "type": "header", "payload": {}}]
    _write(t, rows)
    before = t.read_bytes()

    record = run_migration(server)

    assert t.read_bytes() == before
    assert record["files_changed"] == 0
