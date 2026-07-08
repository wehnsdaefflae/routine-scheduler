"""Transcript JSONL: roundtrip, partial-line hold-back, gzip reads."""

import gzip
import json

from rsched.engine.transcript import Transcript, read_events


def test_roundtrip_and_offsets(tmp_path):
    path = tmp_path / "transcript.jsonl"
    t = Transcript(path)
    t.header(run_id="r:20260708-070000", routine="r", workflow={"slug": "w"},
             orchestrator={"endpoint": "e", "model": "m"})
    t.event("assistant_action", {"kind": "shell", "command": "ls", "say": "s"}, turn=1,
            usage={"in": 1, "out": 2})
    t.event("observation", {"kind": "shell", "exit": 0}, turn=1)
    t.close()

    events, offset = read_events(path)
    assert [e["type"] for e in events] == ["header", "assistant_action", "observation"]
    assert events[1]["usage"] == {"in": 1, "out": 2}
    # tail from offset: nothing new yet
    more, offset2 = read_events(path, offset)
    assert more == [] and offset2 == offset


def test_partial_line_held_back(tmp_path):
    path = tmp_path / "t.jsonl"
    full = json.dumps({"type": "finish", "payload": {}}) + "\n"
    partial = json.dumps({"type": "error", "payload": {}})[:-4]  # no newline, broken JSON
    path.write_text(full + partial, encoding="utf-8")
    events, offset = read_events(path)
    assert len(events) == 1 and offset == len(full.encode())
    # complete the partial line → next read from offset picks it up
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "error", "payload": {}})[-4:] + "\n")
    events2, offset2 = read_events(path, offset)
    assert len(events2) == 1 and events2[0]["type"] == "error" and offset2 > offset


def test_gzip_read(tmp_path):
    path = tmp_path / "t.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "header"}) + "\n")
        fh.write(json.dumps({"type": "finish", "payload": {}}) + "\n")
    events, _ = read_events(path)
    assert [e["type"] for e in events] == ["header", "finish"]
    # plain path that only exists gzipped is found too
    events, _ = read_events(tmp_path / "t.jsonl")
    assert len(events) == 2
