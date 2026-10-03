"""The run rail's OVERSIGHT strip — what the escalation ladder did to this run.

A supervised run is the one a reader most needs explained: turns were spent on a child nobody
asked for and the worker was redirected mid-run. Before this strip the only trace was four
transcript events the console dropped, so the page showed a redirected run with nothing naming
who redirected it.

Two claims are pinned here, and both are about what a PERSON sees:

- a run whose ladder fired shows the rung, the countdown and the verdict, in words;
- a run with no rung gains NO card — the rail's caption list is unchanged. An always-present,
  always-empty card teaches a reader to skip the one place supervision is reported, and
  `tests/ui/test_finish_line.py` pins that caption list exactly.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

LADDER_EVENTS = [
    {"type": "header", "run_id": "uir:20260906-100000"},
    {"type": "oversight_dispatch",
     "payload": {"rung": 1, "reason": "repeated_failure", "turn": 2, "since_turn": 1,
                 "oversight_turns": 11}},
    {"type": "oversight_directive",
     "payload": {"rung": 1, "verdict": "off_track", "disposition": "redirect",
                 "next_rung_in": 6, "next_look": "whether the gate verdict was read back"}},
]


def test_a_supervised_run_shows_its_rung_the_countdown_and_the_verdict(ui, ui_page):
    ts = "20260906-100000"
    run = ui.seed_run("uir", ts, "running", summary="")
    (run / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in LADDER_EVENTS), encoding="utf-8")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    strip = ui_page.locator(".ladderstrip")
    expect(strip).to_be_visible()
    expect(strip).to_contain_text("rung 1: off_track · redirect")
    # counted from the DISPATCH turn the way the engine counts it: 2 + 6 - 2 = 6
    expect(strip).to_contain_text("next escalation in 6 turns")
    expect(strip).to_contain_text("next look: whether the gate verdict was read back")
    expect(strip).to_contain_text("1 dispatched")
    # and the section has its own caption, so it can be folded away like every other
    expect(ui_page.locator('.run-rail .rail-cap[data-rail="oversight"]')).to_be_visible()


def test_a_skipped_rung_says_why_rather_than_showing_nothing(ui, ui_page):
    """A run with the ladder ON and no supervision in it is indistinguishable from a healthy one
    unless the reason survives — that reason is the whole value of the record."""
    ts = "20260906-110000"
    run = ui.seed_run("uir", ts, "finished", summary="done")
    (run / "transcript.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "header", "run_id": f"uir:{ts}"},
        {"type": "oversight_skipped",
         "payload": {"rung": 2, "reason": "the supervise-a-run pattern is not in the library"}},
    ]), encoding="utf-8")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    strip = ui_page.locator(".ladderstrip")
    expect(strip).to_contain_text("rung 2 did not run")
    expect(strip).to_contain_text("the supervise-a-run pattern is not in the library")
    expect(strip).to_contain_text("1 skipped")


def test_a_run_with_no_rung_gains_no_oversight_card(ui, ui_page):
    """The rail's captions are unchanged for the overwhelming majority of runs."""
    ts = "20260906-120000"
    ui.seed_run("uir", ts, "finished", summary="done")
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{ts}")
    expect(ui_page.locator(".run-rail .rail-cap").first).to_be_visible()
    caps = [c.strip().lower() for c in
            ui_page.locator(".run-rail .rail-cap").all_inner_texts()]
    assert "oversight" not in caps
    assert ui_page.locator(".ladderstrip").count() == 0
