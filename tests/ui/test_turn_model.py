"""D188 — the turn says which model answered it, on the page.

Operator, 2026-10-16: "we need to see with each turn which model was used". The engine has
stamped the resolved model per turn for some time (`usage.model`), and nothing in static/ ever
read it: the turn row showed token counts with a tooltip naming the serving PROVIDER, so the
one fact an audit of a model switch needs was in the file and on no surface.

What is asserted here is the TEXT A READER SCANS, never a data-* attribute or a class. A
previous release shipped two adjacent rows both reading "approval" and the whole browser suite
passed because every test reached its target by attribute.

The second test is the direction that rots silently: a run recorded BEFORE the field existed
has no model on its turns, and the row must then say nothing at all — "null" or "undefined" on
a year of archived runs is worse than the blank the reader already understood.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

OLD_TURN = {"type": "assistant_action", "turn": 1, "ts": "2026-09-05T12:00:00+02:00",
            "payload": {"kind": "read_file", "path": "state/x.md", "say": "before the field"},
            "usage": {"in": 10, "out": 4}}
NEW_TURN = {"type": "assistant_action", "turn": 2, "ts": "2026-09-05T12:01:00+02:00",
            "payload": {"kind": "write_file", "path": "state/y.md", "say": "after the field"},
            "usage": {"in": 20, "out": 8, "model": "claude-proxy/claude-opus-5",
                      "role": "main"}}
SWITCHED_TURN = {"type": "assistant_action", "turn": 3, "ts": "2026-09-05T12:02:00+02:00",
                 "payload": {"kind": "finish", "status": "ok", "say": "a switched turn"},
                 "usage": {"in": 30, "out": 9, "model": "openrouter/glm-5",
                           "role": "uncensored"}}


def _seed(ui, events, ts="20260905-120000"):
    run_dir = ui.seed_run("uir", ts, "finished", summary="done")
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in
                [{"type": "header", "run_id": f"uir:{ts}"}, *events]),
        encoding="utf-8")
    return ts


def test_each_turn_shows_the_model_that_answered_it(ui, ui_page):
    """Both turns name their own model, so a run that SWITCHED mid-run is auditable from the
    transcript alone — which is the whole reason the operator asked for this."""
    ts = _seed(ui, [NEW_TURN, SWITCHED_TURN])
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    expect(ui_page.locator(".turn").first).to_be_visible()
    body = ui_page.locator("#view").text_content()

    assert "main → claude-proxy/claude-opus-5" in body
    assert "uncensored → openrouter/glm-5" in body


def test_a_turn_recorded_before_the_field_existed_says_nothing(ui, ui_page):
    """Absent, not "null" and not "undefined": the archive predates the field."""
    ts = _seed(ui, [OLD_TURN], ts="20260905-130000")
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    expect(ui_page.locator(".turn").first).to_be_visible()
    body = ui_page.locator("#view").text_content()

    assert "before the field" in body          # the turn itself rendered
    assert "10 in" in body                     # …with its token counts, as always
    assert "null" not in body
    assert "undefined" not in body
    assert "→" not in body                     # no empty role/model arrow either
