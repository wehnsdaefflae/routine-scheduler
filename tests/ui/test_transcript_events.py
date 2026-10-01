"""Event shapes the transcript renderer had no branch for, against the REAL console.

Every one of these reached the page as something misleading rather than as an error, which is
why none of them was caught by the js_errors collector or by anyone reading code:

- a HELD action fell through to `JSON.stringify` and read as a wall of raw payload, so the one
  observation whose whole job is to be re-read by a person was the least readable on the page;
- all SIX finish-gate rungs rendered as the fabrication guard, the only one that existed when
  the branch was written — so a run deferred for its accounting was labelled a hallucinated
  completion, which is the opposite diagnosis;
- the background archive (0.308.0) carries no before/after chars, because the digest already
  did the shrinking — it reached the branch that prints a span and said "undefined → undefined
  chars"; abandoned, it said "nothing elided this pass", which is the line for a no-op pass;
- `stages_skipped` (F521) had no renderer at all and drew nothing (tests/test_static_transcript.py
  now pins every EVENT_TYPES member to one).
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import Error as PlaywrightError
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
     "payload": {"kind": "finish", "rejected": True,
                 "accounting": {"missing": ["d2", "g1"], "bare": ["d1"],
                                "refused": ["g2: an outcome only the operator judges"]}}},
    {"type": "observation", "turn": 4,
     "payload": {"kind": "finish", "rejected": True, "claims_unsupported": ["d3"]}},
    {"type": "compaction", "turn": 5,
     "payload": {"background": True, "mode": "llm-history", "elided_messages": 30,
                 "history_files": 7}},
    {"type": "compaction", "turn": 6,
     "payload": {"background": True, "archival_abandoned": True, "elided_messages": 12}},
    {"type": "stopping_update",
     "payload": {"met": ["g1"], "judged": {"d1": "met", "g1": "met"},
                 "run_id": "uir:20260905-120000", "disputed": ["g1"]}},
    {"type": "stopping_update",
     "payload": {"goal_reached": True, "run_id": "uir:20260905-120000", "proposal": "p1"}},
    {"type": "stages_skipped",
     "payload": {"declared": ["gather", "draft", "send"], "entered": ["draft"],
                 "skipped": ["gather", "send"], "run_id": "uir:20260905-120000"}},
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
    expect(ui_page.locator(".turn").first).to_be_visible()
    # text_content, not inner_text: an observation body sits inside a collapsed <details>
    # and Chrome's innerText drops what is not rendered. What is asserted here is what the
    # renderer PRODUCED — one click away is still on the page.
    body = ui_page.locator("#view").text_content()

    # the two holds say what was held and how to proceed, in prose
    assert "HELD" in body and "util:fs-ops mv a b" in body
    assert "it overwrites the destination" in body
    assert "commit a checkpoint before the first edit" in body
    assert '{"kind": "reminder_hold"' not in body        # not the raw payload

    # each finish rung is named for what it actually was
    assert "the accounting is incomplete: no verdict for d2, g1 · no note behind d1" in body
    assert "g2: an outcome only the operator judges" in body
    assert 'does not support the "met" claim on d3' in body
    assert "fabrication guard" not in body

    # what the finish recorded and the finish line reached — in words
    assert "accounting: d1 met · g1 met · proved g1" in body
    assert "a check of the transcript disagreed on g1" in body
    assert "the finish line is reached" in body

    # a finish that stood with declared stages never entered (F521) — a notice, in words
    assert "stages skipped: gather, send (entered: draft)" in body

    # the background archive, landed and abandoned — neither prints a span it does not have
    assert "background archive landed: 30 messages" in body
    assert "background archive abandoned" in body
    assert "undefined" not in body
    assert "nothing elided this pass" not in body

    # the two side fields ride the turn beside the note pin instead of hiding in the json fold
    assert "^util:fs-ops mv " in body and "would_have" in body


# A file that runs script when it is opened as a page. Served with its own type (image/svg+xml,
# text/html), fetched with the token, and handed to the browser as a blob — whose URL carries the
# CONSOLE's origin, so opened as a top-level page it would run as the console.
ACTIVE_FILES = {
    "attachments/look.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">'
                            '<script>window.ran = localStorage.getItem("rsched_token")</script>'
                            '<rect width="24" height="24" fill="red"/></svg>',
    "attachments/page.html": '<!doctype html><script>'
                             'window.ran = localStorage.getItem("rsched_token")</script><p>hi</p>',
}


def test_an_attachment_that_could_run_script_opens_sandboxed(ui, ui_page):
    """A message attachment opens full-size in a tab of its own. As a blob that tab is the
    console's own origin, and the console has no other wall: an SVG or HTML file a person
    attached — one saved from a web page, say — read the operator token out of localStorage the
    moment it was opened. Such a file opens through components/blobtab.js, the console's one rule
    for new tabs, inside a sandboxed frame whose origin is opaque; its thumbnail still SHOWS an
    SVG, since an <img> runs no script."""
    ts = "20260905-130000"
    run_dir = ui.seed_run("uir", ts, "finished", summary="done")
    for rel, text in ACTIVE_FILES.items():
        path = ui.routines / "uir" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    event = {"type": "user_injection",
             "payload": {"text": "what do these do?", "attachments": list(ACTIVE_FILES)}}
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")

    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    thumb = ui_page.locator(".att-thumb")
    expect(thumb).to_have_attribute("src", re.compile(r"^blob:"))
    for opener in (thumb, ui_page.locator(".att-file", has_text="page.html")):
        with ui_page.context.expect_page() as info:
            opener.click()
        tab = info.value
        tab.wait_for_load_state()
        expect(tab.locator('iframe[sandbox="allow-scripts"]')).to_have_count(1)
        tab.wait_for_timeout(300)                     # the framed file's script has run
        for frame in tab.frames:
            try:
                ran = frame.evaluate("() => window.ran ?? null")
            except PlaywrightError:                   # a frame mid-navigation says nothing
                continue
            assert ran is None, "the file read the operator token"
        tab.close()
