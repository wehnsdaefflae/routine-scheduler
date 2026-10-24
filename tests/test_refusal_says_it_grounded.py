"""A refusal that performs a side effect the caller needs must SAY it performed it (R2879).

`read_file` on a binary or oversized file refuses — and adds the path to `ctx.seen_paths` on
the refusal itself (`engine/grounding.py` says why), which is exactly what the destruction gate
wants: the move or delete that follows is ALLOWED. That has been true since 0.339.0 and
`tests/test_read_file.py::test_binary_file_is_refused_before_any_decode` pins the mechanism.

What was missing is the sentence. The refusal said only what read_file cannot do, so a run that
did the right sequence — read, refused, move — believed it had gained nothing, spent a turn
being confused, and reported the gate itself as a defect (a conversation's report, folded to
this routine as R2879). A refusal that grounds silently is indistinguishable from one that did
nothing — the same class as a `log.warning` no run reads (F754).

These are assertions about WORDS, which is the point: the message is what every routine reads
at the moment it is confused, and the behaviour behind it needed no change at all. They are
also assertions that the four messages agree, because they now come from one module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import action_ctx
from rsched.engine import fileops, fsops, grounding
from rsched.engine.fileops import do_write_file
from rsched.engine.fsops import do_delete, do_move


def _outside(ctx, tmp_path):
    """A writable directory OUTSIDE the routine's own dir — the only place the gate applies."""
    ext = tmp_path / "project"
    ext.mkdir(parents=True, exist_ok=True)
    ctx.routine.fs_write_roots = [ext]
    return ext


@pytest.mark.parametrize("make", [
    pytest.param(lambda p: p.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 50), id="binary"),
    pytest.param(lambda p: p.write_text("x" * 2500, encoding="utf-8"), id="oversized"),
])
def test_a_refused_read_says_the_look_was_recorded(make_routine, tmp_path, monkeypatch, make):
    monkeypatch.setattr(fileops, "READ_MAX_BYTES", 1000)
    ctx = action_ctx(make_routine(), tmp_path)
    blob = ctx.routine.dir / "clip.mkv"
    make(blob)
    obs = fileops._read_one("clip.mkv", {"max_lines": 1}, ctx)
    assert "error" in obs, obs
    assert "this look is recorded" in obs["error"], obs["error"]
    assert "now allowed" in obs["error"], obs["error"]
    assert str(blob) in ctx.seen_paths          # and the sentence is TRUE


def test_the_destruction_refusal_says_the_refusal_itself_counts(make_routine, tmp_path):
    """The message that sends a run to read_file must not imply the read has to SUCCEED: on a
    season pack it never can, and a run that read 1.5 GB to comply is what wrote this gate."""
    ctx = action_ctx(make_routine(), tmp_path)
    ext = _outside(ctx, tmp_path)
    target = ext / "season.mkv"
    target.write_bytes(b"\x00" * 64)
    for obs in (do_delete({"path": str(target)}, ctx),
                do_move({"src": str(target), "dst": str(ext / "moved.mkv")}, ctx)):
        assert "the REFUSAL itself counts" in obs["error"], obs
    assert target.exists()


def test_the_overwrite_refusal_says_a_refused_read_counts(make_routine, tmp_path):
    """`write_file`'s own grounding gate sends a run to read_file for the same reason and is
    satisfied by the same refused read, so it owes the same clause."""
    ctx = action_ctx(make_routine(), tmp_path)
    ext = _outside(ctx, tmp_path)
    victim = ext / "clip.mkv"
    victim.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 50)
    obs = do_write_file({"path": str(victim), "content": "x"}, ctx)
    assert "OVERWRITES" in obs["error"], obs
    assert "the REFUSAL itself counts" in obs["error"], obs["error"]


def test_the_refused_read_then_really_grounds_the_delete(make_routine, tmp_path, monkeypatch):
    """The loop the report walked, end to end: refuse the read, then delete — allowed, because
    the refusal grounded it. If this stops being true, the new sentences become a lie."""
    monkeypatch.setattr(fileops, "READ_MAX_BYTES", 1000)
    ctx = action_ctx(make_routine(), tmp_path)
    ext = _outside(ctx, tmp_path)
    ctx.routine.fs_read_roots = [ext]
    blob = ext / "clip.mkv"
    blob.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 50)
    assert "never read" in do_delete({"path": str(blob)}, ctx)["error"]
    refused = fileops._read_one(str(blob), {"max_lines": 1}, ctx)
    assert "this look is recorded" in refused["error"], refused
    assert do_delete({"path": str(blob)}, ctx)["removed"] is True


def test_the_four_messages_state_one_fact_from_one_place():
    """The structural half: four messages in two modules depend on WHICH looks count, so a
    future edit to one must not be able to contradict the others (that is why they moved into
    engine/grounding.py). Each carries the shared clause verbatim."""
    assert grounding.WHAT_COUNTS in grounding.OVERWRITE_REFUSAL
    assert grounding.WHAT_COUNTS in grounding.destruction_refusal("deletes")
    assert grounding.REFUSAL_GROUNDS in fileops._binary_error(10)
    for module in (fsops, fileops):
        body = Path(module.__file__).read_text(encoding="utf-8")
        assert "sight-unseen" not in body or "grounding" in body, module.__file__
