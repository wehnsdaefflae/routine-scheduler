"""read_file's memory contract (fileops._read_one): only the requested window is ever held,
a binary or oversized file is refused from a stat and an 8 KiB sniff BEFORE anything is
decoded, and a directory reads as its listing. On 2026-09-14 a run read a 1.5 GB .mkv to
satisfy the read-before-delete gate; the whole file was decoded into a str twice and the
3.4 GB host swap-thrashed for five hours until a physical reset.

The delete/move side of that chain — a directory read grounding the tree's deletion — is
tested with the real RunContext in test_fs_actions.py; the head-truncation contract (F204) in
test_view_image.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.engine import fileops
from rsched.engine.history import seen_paths
from rsched.engine.obs_files import format_files


def _ctx(tmp_path):
    return SimpleNamespace(routine=SimpleNamespace(dir=tmp_path, fs_read_roots=[]),
                           grants=None, seen_paths=set(), read_roots=list)


def _never_read(*_a):
    pytest.fail("the file's text was read — the refusal must come from the stat and the sniff")


# ---- the normal file: shape and window unchanged ---------------------------------------------


def test_windowed_read_of_a_normal_file_is_unchanged(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("".join(f"row {i}\n" for i in range(1, 51)), encoding="utf-8")
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("notes.txt", {"start_line": 11, "max_lines": 5}, ctx)
    assert obs == {"path": "notes.txt", "start_line": 11, "end_line": 15, "total_lines": 50,
                   "content": "row 11\nrow 12\nrow 13\nrow 14\nrow 15", "truncated": False}
    assert str(f) in ctx.seen_paths
    # defaults: from line 1, 200 lines — and total_lines is the real count
    whole = fileops._read_one("notes.txt", {}, ctx)
    assert whole["start_line"] == 1 and whole["end_line"] == 50 and whole["total_lines"] == 50
    assert whole["content"].splitlines()[-1] == "row 50"
    # a window past the end is empty, with the count still real (end_line clamps to it)
    past = fileops._read_one("notes.txt", {"start_line": 100}, ctx)
    assert past["content"] == "" and past["total_lines"] == 50 and past["end_line"] == 50
    # CRLF, a missing final newline, an empty file: numbered as splitlines() numbered them
    (tmp_path / "crlf.txt").write_bytes(b"a\r\nb\r\nc")
    assert fileops._read_one("crlf.txt", {}, ctx)["content"] == "a\nb\nc"
    (tmp_path / "empty.txt").write_bytes(b"")
    empty = fileops._read_one("empty.txt", {}, ctx)
    assert empty["content"] == "" and empty["total_lines"] == 0 and empty["end_line"] == 0
    # the schema's 500-line maximum still caps a larger ask
    (tmp_path / "long.txt").write_text("x\n" * 700, encoding="utf-8")
    assert fileops._read_one("long.txt", {"max_lines": 5000}, ctx)["end_line"] == 500


def test_a_missing_file_is_still_an_ordinary_error(tmp_path):
    obs = fileops._read_one("nope.txt", {}, _ctx(tmp_path))
    assert "No such file" in obs["error"] and "size" not in obs


# ---- the two refusals: decided before any decode ---------------------------------------------


def test_oversized_file_is_refused_from_its_stat(tmp_path, monkeypatch):
    monkeypatch.setattr(fileops, "READ_MAX_BYTES", 1_000)
    monkeypatch.setattr(fileops, "_window", _never_read)
    big = tmp_path / "big.log"
    big.write_text("x" * 2_500, encoding="utf-8")
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("big.log", {}, ctx)
    assert obs["size"] == 2_500                       # the actual size, to reason about
    assert "2,500 bytes exceeds the read_file cap of 1,000 bytes" in obs["error"]
    assert "shell (head / sed -n)" in obs["error"]
    assert "content" not in obs
    assert str(big) in ctx.seen_paths                 # a stat IS a look: the delete gate holds
    # a file exactly at the cap is read
    (tmp_path / "edge.log").write_text("y" * 1_000, encoding="utf-8")
    monkeypatch.setattr(fileops, "_window", lambda lines, s, m: (list(lines)[:m], 1))
    assert fileops._read_one("edge.log", {}, ctx)["total_lines"] == 1


def test_the_cap_stays_far_below_what_hurts_a_small_host():
    # the host that thrashed has 3.4 GB; errors="replace" decoding doubles a binary's size
    assert fileops.READ_MAX_BYTES <= 16 * 1024 * 1024


def test_binary_file_is_refused_before_any_decode(tmp_path, monkeypatch):
    monkeypatch.setattr(fileops, "_window", _never_read)
    blob = tmp_path / "clip.mkv"
    blob.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100 + bytes(range(256)) * 10)
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("clip.mkv", {"max_lines": 1}, ctx)   # the 2026-09-14 action
    assert obs["error"].startswith(f"binary file ({blob.stat().st_size:,} bytes)")
    assert obs["size"] == blob.stat().st_size and "content" not in obs
    assert str(blob) in ctx.seen_paths
    # a NUL past the sniff window is not seen — the cap, not the sniff, bounds that case
    late = tmp_path / "late.bin"
    late.write_bytes(b"t" * (fileops.BINARY_SNIFF_BYTES + 10) + b"\x00")
    monkeypatch.setattr(fileops, "_window", lambda lines, s, m: (list(lines)[:m], 1))
    assert "content" in fileops._read_one("late.bin", {}, ctx)
    # utf-8 text with no NUL is text whatever its extension — and a batched read mixes both
    (tmp_path / "notes.bin").write_text("plain text\n", encoding="utf-8")
    batch = fileops.do_read_file({"paths": ["notes.bin", "clip.mkv"]}, ctx)["files"]
    assert "content" in batch[0] and batch[1]["error"].startswith("binary file")
    rendered = format_files({"kind": "read_file", "files": batch}, "read_file")
    assert "--- clip.mkv FAILED: binary file" in rendered


# ---- a directory reads as its listing --------------------------------------------------------


def test_directory_reads_as_its_listing(tmp_path):
    pack = tmp_path / "Show S01"
    pack.mkdir()
    (pack / "e02.mkv").write_bytes(b"\x00" * 300)
    (pack / "e01.mkv").write_bytes(b"\x00" * 200)
    (pack / "extras").mkdir()
    (pack / "link").symlink_to(pack / "e01.mkv")
    ctx = _ctx(tmp_path)
    obs = fileops._read_one("Show S01", {}, ctx)
    assert obs["directory"] is True and obs["total_lines"] == 4 and obs["truncated"] is False
    assert obs["content"].splitlines() == [
        f"file {200:>12}  e01.mkv", f"file {300:>12}  e02.mkv", f"dir  {'-':>12}  extras/",
        f"link {'-':>12}  link -> {pack / 'e01.mkv'}"]
    assert str(pack) in ctx.seen_paths           # the tree is grounded for delete/move
    # a listing pages like a file
    page = fileops._read_one("Show S01", {"start_line": 3, "max_lines": 1}, ctx)
    assert page["content"] == f"dir  {'-':>12}  extras/" and page["end_line"] == 3
    # the observation names it a listing, single and batched — a file keeps "lines"
    assert format_files({"kind": "read_file", **obs}, "read_file").startswith(
        "OBSERVATION (read_file Show S01, directory listing, entries 1-4 of 4):")
    assert "(directory listing, entries 1-4 of 4) ---" in format_files(
        {"kind": "read_file", "files": [obs]}, "read_file")
    (tmp_path / "t.txt").write_text("one\n", encoding="utf-8")
    file_obs = fileops._read_one("t.txt", {}, ctx)
    assert "directory" not in file_obs
    assert "(read_file t.txt, lines 1-1 of 1):" in format_files(
        {"kind": "read_file", **file_obs}, "read_file")


def test_an_empty_directory_lists_nothing(tmp_path):
    (tmp_path / "empty").mkdir()
    obs = fileops._read_one("empty", {}, _ctx(tmp_path))
    assert obs["directory"] is True and obs["content"] == "" and obs["total_lines"] == 0


# ---- the resume rebuild counts what the live run counted --------------------------------------


def test_resume_rebuild_counts_listings_and_size_refusals():
    """history.seen_paths must ground on resume exactly what fileops grounded live: a
    listing (an ordinary successful read) and a refusal that carries `size`; a refusal
    without one (a missing file, a root refusal) stays unseen."""
    events = [
        {"type": "observation", "payload": {
            "kind": "read_file", "path": "a.txt", "start_line": 1, "end_line": 1,
            "total_lines": 1, "content": "x", "truncated": False}},
        {"type": "observation", "payload": {
            "kind": "read_file", "path": "/srv/videos/pack", "directory": True,
            "start_line": 1, "end_line": 2, "total_lines": 2, "content": "…",
            "truncated": False}},
        {"type": "observation", "payload": {"kind": "read_file", "files": [
            {"path": "/srv/videos/e1.mkv", "size": 1_500_000_000, "error": "binary file …"},
            {"path": "/srv/videos/missing.mkv", "error": "[Errno 2] No such file"}]}},
    ]
    assert seen_paths(events) == ["a.txt", "/srv/videos/pack", "/srv/videos/e1.mkv"]
