"""The run rail NAVIGATES (D193): a phase node and a sub-run row each take the reader to the
turn they name, in the real console.

The rail was a readout — it said a phase ran 2 turns and never said which, so the reader
scrolled the transcript hunting for the divider. These cases load a run page and assert the
thing a person actually uses: the node is a LINK, clicking it lands on that turn's box, and the
URL then says where the reader is (so the position is shareable and survives a reload).

Asserting the <a> alone would not do it — a link whose target does not exist on the page looks
identical in the DOM. Each case therefore follows the click through to the turn box being
scrolled to and flashed (static/landing.js `.ref-flash`), which only happens when the address
resolved to a real box.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import expect

SLUG = "uir"
FLASH = re.compile(r"\bref-flash\b")   # static/landing.js lights what a journey landed on


def _seed(ui, ts, *, phase="", events=()):
    run_dir = ui.seed_run(SLUG, ts, "finished", summary="done", phase=phase)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return run_dir


def _action(turn, phase, say, kind="util"):
    return {"ts": f"2026-07-15T10:{turn:02d}:00+00:00", "type": "assistant_action",
            "turn": turn, "phase": phase, "usage": {"in": 100, "out": 10},
            "payload": {"kind": kind, "say": say, "name": "code-search"}}


def test_a_phase_node_is_a_link_that_lands_on_the_turn_the_phase_begins_at(ui, ui_page):
    """Clicking the `act` node scrolls the transcript to turn 3 — the FIRST turn recorded
    under that phase (readmodels/statemap `first_turn`) — and flashes it, and the URL carries
    `?turn=3` so the position is shareable."""
    stages = ui.routine_dir(SLUG) / "stages"
    stages.mkdir(exist_ok=True)
    for name in ("gather", "act"):
        (stages / f"{name}.md").write_text(f"# Step: {name}\n", encoding="utf-8")
    (ui.routine_dir(SLUG) / "main.md").write_text(
        "## Run flow\n1. `stages/gather.md` — g.\n2. `stages/act.md` — a.\n", encoding="utf-8")
    ts = "20260715-150000"
    _seed(ui, ts, phase="act", events=[
        {"ts": "2026-07-15T10:00:00+00:00", "type": "header", "payload": {},
         "run_id": f"{SLUG}:{ts}"},
        _action(1, "gather", "looking around"),
        _action(2, "gather", "still looking"),
        _action(3, "act", "applying the fix"),
        _action(4, "act", "checking it"),
    ])

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/{SLUG}:{ts}")

    node = ui_page.locator("a.pg-node", has_text="act")
    expect(node).to_be_visible()
    assert node.get_attribute("data-turn-link") == "3"      # the phase's FIRST turn, not its last
    node.click()

    landed = ui_page.locator('.turn[data-turn="3"]')
    expect(landed).to_have_class(FLASH)
    expect(ui_page.locator('.turn[data-turn="3"] .saytext')).to_contain_text("applying the fix")
    assert "turn=3" in ui_page.evaluate("location.hash")


def test_a_phase_with_no_recorded_turn_is_not_a_link(ui, ui_page):
    """A phase the run never entered has nowhere to send anyone, so it stays a plain node —
    a link that dead-ends is worse than none."""
    stages = ui.routine_dir(SLUG) / "stages"
    stages.mkdir(exist_ok=True)
    for name in ("gather", "never"):
        (stages / f"{name}.md").write_text(f"# Step: {name}\n", encoding="utf-8")
    (ui.routine_dir(SLUG) / "main.md").write_text(
        "## Run flow\n1. `stages/gather.md` — g.\n2. `stages/never.md` — n.\n", encoding="utf-8")
    ts = "20260715-151000"
    _seed(ui, ts, phase="gather", events=[_action(1, "gather", "only phase")])

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/{SLUG}:{ts}")

    expect(ui_page.locator("a.pg-node", has_text="gather")).to_be_visible()
    expect(ui_page.locator(".pg-node", has_text="never")).to_be_visible()
    expect(ui_page.locator("a.pg-node", has_text="never")).to_have_count(0)


def test_a_sub_run_row_links_to_the_turn_that_started_it(ui, ui_page):
    """The sub-runs card is navigation too: its row lands on the parent turn whose action
    started the child (readmodels/subruntree `start_turn`)."""
    ts = "20260715-152000"
    _seed(ui, ts, phase="act", events=[
        {"ts": "2026-07-15T10:00:00+00:00", "type": "header", "payload": {},
         "run_id": f"{SLUG}:{ts}"},
        _action(1, "act", "orienting"),
        _action(2, "act", "starting the child", kind="spawn"),
        {"ts": "2026-07-15T10:02:30+00:00", "type": "subrun_start",
         "payload": {"n": 1, "label": "crawl", "workflow": "general-task",
                     "mode": "parallel", "depth": 1,
                     "budget": {"turns": 20, "tokens": 0}}},
        {"ts": "2026-07-15T10:09:00+00:00", "type": "subrun_end",
         "payload": {"n": 1, "label": "crawl", "mode": "parallel", "status": "ok",
                     "turns": 7, "usage": {"in": 10, "out": 2}, "summary": "crawled"}},
    ])

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/{SLUG}:{ts}")

    row = ui_page.locator("a.sr-row", has_text="crawl")
    expect(row).to_be_visible()
    assert row.get_attribute("data-turn-link") == "2"
    row.click()

    expect(ui_page.locator('.turn[data-turn="2"]')).to_have_class(FLASH)
    assert "turn=2" in ui_page.evaluate("location.hash")


def test_a_turn_deep_link_lands_on_load(ui, ui_page):
    """`#/run/<id>?turn=N` opens the run ON that turn — the address a rail click writes is
    one a reader can paste to a colleague."""
    ts = "20260715-153000"
    _seed(ui, ts, phase="act", events=[
        {"ts": "2026-07-15T10:00:00+00:00", "type": "header", "payload": {},
         "run_id": f"{SLUG}:{ts}"},
        _action(1, "act", "first move"),
        _action(2, "act", "second move"),
    ])

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/{SLUG}:{ts}?turn=2")

    expect(ui_page.locator('.turn[data-turn="2"]')).to_have_class(FLASH)
