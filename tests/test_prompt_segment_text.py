"""R3341: clicking a prompt segment shows the text — for the two segments that HAVE one.

The operator asked *"can we click on it to see the actual prompt?"*. Half the bar can answer
honestly and half cannot, and these tests pin which half and what the other half says:

1. `history` and `observations` ARE the message list, which is on disk in the transcript, so
   their text is served as a RECORDING — replayed through `engine/history.replay_messages`, the
   engine's own renderer, which is why it is byte-identical to what was sent;
2. every always-on section answers "text not recorded" and no text at all. **Nothing
   re-composes a section at read time**, at any fidelity: the composing path drains a shared
   store and writes transcript events, so a GET endpoint that did it would mutate state on
   every tap of the bar;
3. a row's token figure SUMS to the segment total the bar draws (`estimate_input_tokens` is
   additive), which is what makes the view auditable against the bar rather than adjacent to it;
4. the turn on each row is READ from the transcript, never derived.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.engine.compaction import estimate_input_tokens
from rsched.engine.promptsegments import GROWN_SEGMENTS
from rsched.readmodels import memo, promptsegtext

TRANSCRIPT = [
    {"type": "header", "turn": 0, "payload": {"routine": "r"}},
    {"type": "assistant_action", "turn": 1,
     "payload": {"say": "looking", "kind": "read_file", "path": "a.txt"}},
    # A REAL observation payload, not a sketch: `tests/render_guard.py` makes the engine's own
    # renderer raise on a result missing a key its wording needs (`read_file` without `path`),
    # which is the right guard — a replay that cannot render is the resume bug it exists to
    # catch — and my first fixture tripped it.
    {"type": "observation", "turn": 1,
     "payload": {"kind": "read_file", "path": "a.txt", "content": "A" * 900,
                 "start_line": 1, "end_line": 1, "total_lines": 1}},
    {"type": "assistant_action", "turn": 2,
     "payload": {"say": "writing", "kind": "write_file", "path": "b.txt"}},
    {"type": "observation", "turn": 2,
     "payload": {"kind": "write_file", "path": "b.txt", "bytes": 12}},
]


def _run(tmp_path: Path) -> Path:
    """The run dir, seeded once — IDEMPOTENT, because a test that reads two segments calls this
    twice and `mkdir(parents=True)` without `exist_ok` fails the second time.
    """
    run_dir = tmp_path / "routine" / "runs" / "20261111-140003"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in TRANSCRIPT), encoding="utf-8")
    memo.reset()
    return run_dir


def test_a_grown_segment_is_served_as_a_recording(tmp_path):
    """`history` is what the model said — replayed, not re-composed, and labelled as such."""
    page = promptsegtext.page(_run(tmp_path), "history")
    assert page["recorded"] is True
    assert "what was sent" in page["note"] and "transcript" in page["note"]
    assert page["total"] == 2, page
    assert [r["turn"] for r in page["rows"]] == [2, 1], "newest first, turns read not derived"
    assert "write_file" in page["rows"][0]["preview"], page["rows"][0]


def test_the_observations_segment_carries_the_engines_own_rendering(tmp_path):
    """The point of replaying through `history.replay_messages`: the row holds what the MODEL
    read, rendered by the same function the live path used — not a second rendering of the
    payload that could differ by a label or a newline.
    """
    page = promptsegtext.page(_run(tmp_path), "observations")
    assert page["recorded"] is True and page["total"] == 2
    body = promptsegtext.body(_run(tmp_path), "observations", page["rows"][-1]["i"])
    assert "A" * 50 in body["text"], "the observation's own text, as the model saw it"
    assert body["turn"] == 1


def test_an_always_on_section_says_its_text_is_not_recorded_and_shows_none(tmp_path):
    """The honest refusal. A section labelled as the prompt that is NOT the prompt is worse
    than no feature, so there is no hedged middle ground here: no text, and the reason.
    """
    for name in ("harness", "schema", "instruction", "capabilities", "digest", "reports"):
        page = promptsegtext.page(_run(tmp_path), name)
        assert page["recorded"] is False, name
        assert page["rows"] == [] and page["total"] == 0, name
        assert "never stored" in page["note"], name
        body = promptsegtext.body(_run(tmp_path), name, 0)
        assert body["text"] == "" and body["recorded"] is False


def test_a_rows_tokens_sum_to_the_segment_total(tmp_path):
    """What makes the view auditable against the bar: `estimate_input_tokens` is additive per
    message, so the rows' figures add up to the segment's — the same number the bar draws.
    """
    run_dir = _run(tmp_path)
    page = promptsegtext.page(run_dir, "observations")
    assert sum(int(r["tokens"]) for r in page["rows"]) == page["tokens"]
    whole = [{"content": promptsegtext.body(run_dir, "observations", r["i"])["text"]}
             for r in page["rows"]]
    assert page["tokens"] == sum(estimate_input_tokens([m]) for m in whole)


def test_a_body_is_a_second_call_so_the_rows_stay_small(tmp_path):
    """Rows carry a cut preview; the whole text is fetched for ONE row. `history` and
    `observations` run to hundreds of thousands of tokens, and this console is read on a phone.
    """
    run_dir = _run(tmp_path)
    row = next(r for r in promptsegtext.page(run_dir, "observations")["rows"]
               if r["chars"] > promptsegtext.PREVIEW_CHARS)
    assert len(row["preview"]) == promptsegtext.PREVIEW_CHARS and row["truncated"] is True
    assert len(promptsegtext.body(run_dir, "observations", row["i"])["text"]) == row["chars"]


def test_the_segment_names_are_the_measurements_own(tmp_path):
    """ONE mapping, not two: the segments this serves are exactly the ones
    `engine/promptsegments` measures as grown. A second copy would let the view offer a click
    the server refuses, or hide one it could honour.
    """
    for name in set(GROWN_SEGMENTS.values()):
        assert promptsegtext.page(_run(tmp_path), name)["recorded"] is True


def test_a_missing_row_index_is_said_rather_than_raised(tmp_path):
    """A stale link (the run grew, or the reader kept a page open) must not 500."""
    out = promptsegtext.body(_run(tmp_path), "history", 999)
    assert out["missing"] is True and out["text"] == ""


def test_a_run_with_no_transcript_reads_as_empty(tmp_path):
    """The rail polls this while a run is live, so the first tick may precede any event."""
    empty = tmp_path / "runs" / "x"
    empty.mkdir(parents=True)
    memo.reset()
    page = promptsegtext.page(empty, "history")
    assert page["recorded"] is True and page["rows"] == [] and page["total"] == 0
