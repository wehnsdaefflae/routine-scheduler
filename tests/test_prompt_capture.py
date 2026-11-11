"""The always-on prompt's section TEXTS, captured once at boot — the operator's option B.

His authorization, verbatim: *"Also store the always-on section texts ONCE at boot in the run
dir, so the whole bar becomes clickable and truthful — a new persistence of prompt text
(~60-250 kB per run), touching no byte of what is sent and no cache test"*.

Every bound in that sentence is a test here, because each one is what makes the write safe:

1. the file lands in the RUN DIR and carries every composed section's text;
2. **not one byte of what is sent changes** — the capture reads the tuples the composer already
   built, and `tests/test_prompt_segments.py` holds the request itself to byte identity;
3. ONCE per leg, not per turn — a second turn adds nothing;
4. a leg that RE-COMPOSES (a resume) overwrites it, so the file describes the prompt the current
   leg is sending rather than an older one;
5. the capture is BEST-EFFORT: a run whose capture cannot be written still boots, and the view
   then says "text not recorded" — which is where every run recorded before this shipped is.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.engine import promptcapture
from rsched.readmodels import memo, promptsegtext

SECTIONS = [("harness", "H" * 2000), ("schema", "S" * 500),
            ("instruction", "do the thing"), ("digest", "state: fresh")]


def test_the_capture_lands_in_the_run_dir_with_every_section(tmp_path):
    promptcapture.write(tmp_path, SECTIONS)
    assert promptcapture.path(tmp_path).name == promptcapture.FILE
    raw = json.loads(promptcapture.path(tmp_path).read_text(encoding="utf-8"))
    assert [r["name"] for r in raw["sections"]] == [n for n, _ in SECTIONS], "prompt order"
    assert raw["sections"][0]["chars"] == 2000
    back = promptcapture.read(tmp_path)
    assert back["instruction"]["text"] == "do the thing"
    assert back["harness"]["truncated"] is False


def test_a_second_write_replaces_rather_than_appends(tmp_path):
    """ONCE per leg: a resumed leg re-composes its sections, so the file must describe the
    CURRENT leg's prompt — not carry two copies, and not keep a stale one.
    """
    promptcapture.write(tmp_path, SECTIONS)
    promptcapture.write(tmp_path, [("instruction", "a different task")])
    back = promptcapture.read(tmp_path)
    assert set(back) == {"instruction"} and back["instruction"]["text"] == "a different task"


def test_an_unwritable_run_dir_never_raises(tmp_path):
    """Best-effort by construction: a diagnostic may not be why a run fails to start."""
    promptcapture.write(tmp_path / "does" / "not" / "exist", SECTIONS)   # must not raise
    assert promptcapture.read(tmp_path / "does" / "not" / "exist") == {}


def test_a_run_with_no_capture_reads_as_nothing_recorded(tmp_path):
    """Every run booted before this shipped, and the state the view must label honestly."""
    assert promptcapture.read(tmp_path) == {}
    page = promptsegtext.page(tmp_path, "harness")
    assert page["recorded"] is False and "never stored" in page["note"]


def test_a_captured_section_is_served_and_labelled_as_this_legs_composed_text(tmp_path):
    """The whole point of option B: the always-on half of the bar becomes readable — and says
    WHICH prompt it is, since the always-on prompt is re-sent verbatim every turn.
    """
    run_dir = tmp_path / "runs" / "20261111-140003"
    run_dir.mkdir(parents=True)
    promptcapture.write(run_dir, SECTIONS)
    memo.reset()
    page = promptsegtext.page(run_dir, "harness")
    assert page["recorded"] is True
    assert "composed section" in page["note"] and "every turn of this leg" in page["note"]
    assert page["total"] == 1 and page["rows"][0]["chars"] == 2000
    body = promptsegtext.body(run_dir, "harness", 0)
    assert body["text"] == "H" * 2000 and body["recorded"] is True


def test_an_oversized_section_is_cut_and_says_so(tmp_path):
    """A bound against a pathological instruction making the capture the biggest file in the
    run. Nothing real is near it (the two largest sections are ~30 kB each).
    """
    promptcapture.write(tmp_path, [("digest", "D" * (promptcapture.SECTION_MAX + 10))])
    row = promptcapture.read(tmp_path)["digest"]
    assert row["truncated"] is True
    assert len(row["text"]) == promptcapture.SECTION_MAX
    assert row["chars"] == promptcapture.SECTION_MAX + 10, "the TRUE size, not the kept size"


def test_the_capture_is_written_from_the_same_tuples_the_measurement_reads(tmp_path):
    """ONE source for the text and the token count, so they can never describe different
    prompts — `note_sections` is boot's single call over the composed sections.
    """
    from types import SimpleNamespace

    from rsched.engine import promptsegments

    ctx = SimpleNamespace(run_dir=tmp_path)
    chars, tokens = promptsegments.note_sections(ctx, SECTIONS)
    assert chars == {n: len(t) for n, t in SECTIONS}
    assert set(tokens) == {n for n, _ in SECTIONS}
    assert promptcapture.read(tmp_path)["harness"]["chars"] == chars["harness"]


def test_the_captured_text_is_what_the_composer_built(tmp_path: Path):
    """No normalization, no re-wrapping, no stripping: a byte that differs makes the view a
    paraphrase of the prompt rather than the prompt.
    """
    odd = [("instruction", " leading and trailing \n\n ragged\ttabs \u2028and a line sep ")]
    promptcapture.write(tmp_path, odd)
    assert promptcapture.read(tmp_path)["instruction"]["text"] == odd[0][1]
