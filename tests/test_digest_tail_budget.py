"""The digest's history tails are bounded in CHARACTERS, and say so when they clip.

Goal b5 step 3. The digest is the largest section of the composed prompt (29,122 chars over 37
live captures, 2026-11-13) and four history tails carried 69% of it. Each was already capped —
in LINES. A note line is a paragraph and a ledger entry is a whole run summary, so a "10-line"
tail measured 5,508 chars at the median and an index reached 23,069.

What these tests hold is the PROPERTY, not a number (`engine/digesttail.TAIL_BUDGET` owns the
number, in one place, with the measurement behind it):

1. every history tail stays within the budget, however large its source;
2. a clipped block SAYS it was clipped and names the file that holds it whole — a run that
   cannot tell a clipped block from a complete one has to read the source to find out, which
   is the cost being removed;
3. the NEWEST history survives when the budget bites (notes and ledger), because a digest's
   purpose is to carry what just happened;
4. nothing is clipped when it already fits — no marker, no behaviour change, byte for byte.
"""

from __future__ import annotations

from rsched import runledger
from rsched.engine import digesttail, notemeta, notes


def test_a_block_that_fits_is_untouched():
    """The common case must be byte-identical: most tails are already small."""
    text = "a line\nanother line\n" * 5
    assert digesttail.clip(text) == text
    assert "clipped" not in digesttail.clip(text)


def test_a_clipped_block_says_so_and_names_where_the_whole_is():
    body = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
    out = digesttail.clip(body, read_with="state/notes.md")
    assert len(out) <= digesttail.TAIL_BUDGET + 200, "the marker must not blow the budget"
    assert "more characters clipped" in out
    assert "state/notes.md holds it whole" in out, (
        "a clipped block that does not name its source makes the run read the source to find "
        "out what it is missing — which is the cost this bound exists to remove")


def test_a_clip_lands_on_a_line_boundary():
    """A half-sentence reads as the engine having lost the rest."""
    body = "\n".join("x" * 60 for _ in range(200))
    out = digesttail.clip(body)
    kept = out.split("\n[...")[0]
    assert all(line == "x" * 60 for line in kept.splitlines() if line)


def test_a_budget_below_the_floor_is_not_a_clip():
    """Below MIN_BUDGET the marker costs more than the clip saves, so nothing happens."""
    body = "y" * 500
    assert digesttail.clip(body, budget=10) == body


def test_the_notes_tail_keeps_the_newest_notes(tmp_path):
    """A run needs what just happened; the older half is one read away."""
    d = tmp_path / "r"
    (d / "state").mkdir(parents=True)
    lines = [f"- [stamp {i}] " + "n" * 400 for i in range(40)]
    (d / "state" / "notes.md").write_text("\n".join(lines), encoding="utf-8")
    out = notes.tail(d)
    assert len(out) <= digesttail.TAIL_BUDGET + 200
    assert "[stamp 39]" in out, "the newest note was dropped"
    assert "[stamp 0]" not in out
    assert "clipped" in out and "state/notes.md" in out


def test_the_notes_tail_is_untouched_when_it_fits(tmp_path):
    d = tmp_path / "r"
    (d / "state").mkdir(parents=True)
    (d / "state" / "notes.md").write_text("- one\n- two\n- three\n", encoding="utf-8")
    assert notes.tail(d) == "- one\n- two\n- three"


def test_the_ledger_tail_keeps_the_newest_entries(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    entries = "".join(
        f"### 2026110{i % 10}-120000 · ok · 10 turns\n\n" + ("summary " * 60) + "\n\n"
        for i in range(12))
    (d / runledger.FILE).write_text("# Ledger\n\n" + entries, encoding="utf-8")
    out = runledger.digest_tail(d)
    assert len(out) <= digesttail.TAIL_BUDGET + 400, "heading + marker must stay bounded"
    assert out.startswith("LEDGER — the engine's record"), "the heading is the orientation"
    assert "more characters of older entries clipped" in out
    assert runledger.FILE in out


def test_the_memory_index_block_is_bounded(tmp_path):
    """A mature store measured 23,069 chars here — the largest block in any live digest."""
    index = "\n".join(f"- note-{i}.md: " + ("what this note holds, " * 12) for i in range(60))
    blocks = notemeta.digest_blocks(index)
    assert blocks, "an index with entries must still produce its block"
    assert len(blocks[0]) <= digesttail.TAIL_BUDGET + 300
    assert ".memory/INDEX.md" in blocks[0], "the run must be told where the whole index is"


def test_a_small_memory_index_is_shown_whole(tmp_path):
    index = "- one.md: the first\n- two.md: the second\n"
    blocks = notemeta.digest_blocks(index)
    assert "one.md" in blocks[0] and "two.md" in blocks[0]
    assert "clipped" not in blocks[0]
