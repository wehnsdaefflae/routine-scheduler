"""Event shapes the transcript renderer had no branch for, against the REAL console.

Every one of these reached the page as something misleading rather than as an error, which is
why none of them was caught by the js_errors collector or by anyone reading code:

- a HELD action fell through to `JSON.stringify` and read as a wall of raw payload, so the one
  observation whose whole job is to be re-read by a person was the least readable on the page;
- all SIX finish-gate rungs rendered as the fabrication guard, the only one that existed when
  the branch was written — so a run deferred for an open stopping condition was labelled a
  hallucinated completion, which is the opposite diagnosis;
- the background archive (0.308.0) carries no before/after chars, because the digest already
  did the shrinking — it reached the branch that prints a span and said "undefined → undefined
  chars"; abandoned, it said "nothing elided this pass", which is the line for a no-op pass.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

EVENTS = [
    {"type": "header", "run_id": "uir:20260905-120000"},
    {"type": "assistant_action", "turn": 1,
     "payload": {"kind": "util", "name": "fs-ops", "args": ["mv", "a", "b"],
                 "say": "moving it",
                 "remind": {"op": "add", "regex": "^util:fs-ops mv ",
                            "description": "it overwrites the destination", "scope": "local"},
                 "remind_feedback": {"id": "rem-mv", "label": "would_have"}}},
    {"type": "observation", "turn": 1,
     "payload": {"kind": "reminder_hold", "action": "util:fs-ops mv a b",
                 "reminders": [{"id": "rem-mv", "scope": "local",
                                "description": "it overwrites the destination"}]}},
    {"type": "observation", "turn": 2,
     "payload": {"kind": "assist_hold", "action": "write_file path=/repo/x",
                 "lines": ["commit a checkpoint before the first edit"]}},
    {"type": "observation", "turn": 3,
     "payload": {"kind": "finish", "rejected": True, "stopping_unaccounted": ["s1", "s2"]}},
    {"type": "compaction", "turn": 4,
     "payload": {"background": True, "mode": "llm-history", "elided_messages": 30,
                 "history_files": 7}},
    {"type": "compaction", "turn": 5,
     "payload": {"background": True, "archival_abandoned": True, "elided_messages": 12}},
]


def _seed(ui, ts="20260905-120000"):
    run_dir = ui.seed_run("uir", ts, "finished", summary="done")
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    return ts


def test_the_transcript_renders_every_event_shape_in_words(ui, ui_page):
    ts = _seed(ui)
    ui_page.set_viewport_size({"width": 1400, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    expect(ui_page.locator(".turn").first).to_be_visible(timeout=10_000)
    # text_content, not inner_text: an observation body sits inside a collapsed <details>
    # and Chrome's innerText drops what is not rendered. What is asserted here is what the
    # renderer PRODUCED — one click away is still on the page.
    body = ui_page.locator("#view").text_content()

    # the two holds say what was held and how to proceed, in prose
    assert "HELD" in body and "util:fs-ops mv a b" in body
    assert "it overwrites the destination" in body
    assert "commit a checkpoint before the first edit" in body
    assert '{"kind": "reminder_hold"' not in body        # not the raw payload

    # the finish rung is named for what it actually was
    assert "does not account for open stopping conditions: s1, s2" in body
    assert "fabrication guard" not in body

    # the background archive, landed and abandoned — neither prints a span it does not have
    assert "background archive landed: 30 messages" in body
    assert "background archive abandoned" in body
    assert "undefined" not in body
    assert "nothing elided this pass" not in body

    # the two side fields ride the turn beside the note pin instead of hiding in the json fold
    assert "^util:fs-ops mv " in body and "would_have" in body
