"""Transcript JSONL: roundtrip, partial-line hold-back, gzip reads, the on_event
observer, and malformed-line skipping (logged, never silent)."""

import gzip
import json
import logging

from rsched.engine.transcript import Transcript, read_events


def test_roundtrip_and_offsets(tmp_path):
    path = tmp_path / "transcript.jsonl"
    t = Transcript(path)
    t.header(run_id="r:20260708-070000", routine="r", workflow={"slug": "w"},
             orchestrator={"endpoint": "e", "model": "m"})
    t.event("assistant_action", {"kind": "util", "name": "ls", "say": "s"}, turn=1,
            usage={"in": 1, "out": 2})
    t.event("observation", {"kind": "util", "name": "ls", "exit": 0}, turn=1)
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
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "error", "payload": {}})[-4:] + "\n")
    events2, offset2 = read_events(path, offset)
    assert len(events2) == 1 and events2[0]["type"] == "error" and offset2 > offset


def test_on_event_observes_every_write(tmp_path):
    """The optional on_event callback sees every event object after it hits disk —
    the CLI's live stream rides on it (no write-path override needed)."""
    seen = []
    t = Transcript(tmp_path / "t.jsonl", on_event=seen.append)
    t.header(run_id="r:1", routine="r", workflow={"slug": "w"},
             orchestrator={"endpoint": "e", "model": "m"})
    t.event("finish", {"status": "ok", "summary": "s"})
    t.close()
    assert [o["type"] for o in seen] == ["header", "finish"]
    events, _ = read_events(tmp_path / "t.jsonl")
    assert [e["type"] for e in events] == ["header", "finish"]   # disk saw the same events


def test_malformed_line_skipped_with_log_trace(tmp_path, caplog):
    path = tmp_path / "t.jsonl"
    good = json.dumps({"type": "header"}) + "\n"
    good2 = json.dumps({"type": "finish", "payload": {}}) + "\n"
    path.write_text(good + "{this is not json}\n" + good2, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="rsched.transcript"):
        events, offset = read_events(path)
    assert [e["type"] for e in events] == ["header", "finish"]
    assert offset == len((good + "{this is not json}\n" + good2).encode())  # bytes still counted
    assert "skipping malformed line" in caplog.text


def test_a_line_that_is_json_but_not_an_object_is_skipped(tmp_path, caplog):
    """Every reader does `ev.get(...)`: one valid-JSON line that is a list, a string or a
    number used to come back as an "event" and crash tasktree, fileactivity and statemap."""
    path = tmp_path / "t.jsonl"
    body = (json.dumps({"type": "header"}) + "\n" + '["a", "list"]\n"a string"\n42\nnull\n'
            + json.dumps({"type": "finish", "payload": {}}) + "\n")
    path.write_text(body, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="rsched.transcript"):
        events, offset = read_events(path)
    assert [e["type"] for e in events] == ["header", "finish"]
    assert offset == len(body.encode())
    assert caplog.text.count("skipping malformed line") == 4


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


def test_a_rotated_transcript_keeps_events_quoting_unicode_line_separators(tmp_path):
    """The writer dumps with ensure_ascii=False, which leaves U+2028, U+2029 and U+0085 RAW
    inside a line — and scraped web text carries them. `str.splitlines()` breaks a line at
    every one of them, so the gz path cut those events in half and dropped both halves as
    malformed: a transcript lost them the moment retention gzipped it."""
    plain = tmp_path / "transcript.jsonl"
    t = Transcript(plain)
    for text in ("line separator", "paragraph separator", "next\x85line"):
        t.event("observation", {"kind": "util", "stdout": text})
    t.event("finish", {"status": "ok", "summary": "done"})
    t.close()
    live, _ = read_events(plain)
    with plain.open("rb") as src, gzip.open(tmp_path / "transcript.jsonl.gz", "wb") as dst:
        dst.write(src.read())
    plain.unlink()                         # what retention does (registry._gzip_in_place)

    rotated, _ = read_events(plain)
    assert rotated == live
    assert [e["payload"].get("stdout") for e in rotated[:3]] == [
        "line separator", "paragraph separator", "next\x85line"]


def test_a_line_cut_inside_a_multibyte_character_is_held_back(tmp_path):
    """A tailer polls while the engine writes. A partial last line is held back by design —
    but text-mode decoding raised UnicodeDecodeError when the cut fell INSIDE a multi-byte
    character (an em dash is three bytes, and the engine's own prose is full of them), so the
    read crashed instead of waiting for the rest of the line."""
    path = tmp_path / "t.jsonl"
    full = (json.dumps({"type": "finish", "payload": {}}) + "\n").encode()
    line = json.dumps({"type": "error", "payload": {"message": "a — b"}},
                      ensure_ascii=False).encode() + b"\n"
    cut = line.index("—".encode()) + 1          # one byte into the em dash
    path.write_bytes(full + line[:cut])

    events, offset = read_events(path)
    assert [e["type"] for e in events] == ["finish"] and offset == len(full)
    with path.open("ab") as fh:                      # the writer finishes the line
        fh.write(line[cut:])
    events2, offset2 = read_events(path, offset)
    assert events2[0]["payload"]["message"] == "a — b"
    assert offset2 == len(full) + len(line)


def test_a_rotated_transcript_honours_the_offset(tmp_path):
    """An offset is a byte position in the transcript, and the gz file holds the SAME bytes —
    so a tailer that read the plain file up to N and then met the rotated one must get only
    what follows N, not the whole run again."""
    first = (json.dumps({"type": "header"}) + "\n").encode()
    second = (json.dumps({"type": "finish", "payload": {}}) + "\n").encode()
    with gzip.open(tmp_path / "t.jsonl.gz", "wb") as fh:
        fh.write(first + second)

    events, offset = read_events(tmp_path / "t.jsonl", len(first))
    assert [e["type"] for e in events] == ["finish"]
    assert offset == len(first) + len(second)
    assert read_events(tmp_path / "t.jsonl", offset) == ([], offset)   # nothing new, ever
