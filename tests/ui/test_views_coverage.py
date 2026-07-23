"""Help + Log views and the transcript renderer's lifecycle events - the pages/branches
the UI suite never exercised (findings ledger COVERAGE items).
"""

import json
import time

from playwright.sync_api import expect


def test_help_view_renders_docs_state(ui, ui_page):
    """The Help tab renders whichever docs state the machine has: the page chips + iframe
    when a build exists (docs_out_dir is per-user, so a dev box usually has one), the
    still-being-generated empty state otherwise - and never a blank page or a JS error
    (the ui_page teardown asserts the console stayed clean)."""
    ui_page.goto(f"{ui.url}/#/help")
    ui_page.wait_for_selector("h1:has-text('Help')", timeout=10_000)
    ui_page.wait_for_selector(".filterbar .tag, .empty .t", timeout=10_000)
    if ui_page.locator(".empty .t").count():
        expect(ui_page.locator(".empty .t")).to_contain_text(
            "documentation is still being generated")
    else:
        expect(ui_page.locator("iframe.help-frame")).to_be_visible()


def test_log_view_lists_runs_with_stats_strip(ui, ui_page):
    # a RECENT ts - the feed default window is relative to now and hides old runs
    ts = time.strftime("%Y%m%d-070000")
    ui.seed_run("uir", ts, "finished", summary="all done")
    ui_page.goto(f"{ui.url}/#/log")
    ui_page.wait_for_selector("h1:has-text('Log')", timeout=10_000)
    expect(ui_page.locator(".stats .stat").first).to_be_visible(timeout=10_000)
    expect(ui_page.locator(".feed")).to_contain_text("uir", timeout=10_000)


def test_transcript_renders_lifecycle_events(ui, ui_page):
    """question / answer / error / compaction transcript events all render as their own
    rows in the run view (the SIMPLE renderer map - previously untested)."""
    run_dir = ui.seed_run("uir", "20260714-070000", "finished", summary="done")
    events = [
        {"ts": "t", "type": "question", "turn": 1,
         "payload": {"qid": "q1", "mode": "deferred", "type": "text",
                     "question": "Which path should I take?", "default": "A"}},
        {"ts": "t", "type": "answer", "turn": 1,
         "payload": {"qid": "q1", "source": "web", "text": "take B"}},
        {"ts": "t", "type": "error", "turn": 2,
         "payload": {"where": "endpoint", "attempt": 1, "message": "boom"}},
        {"ts": "t", "type": "compaction", "turn": 3,
         "payload": {"before_chars": 9000, "after_chars": 1000}},
    ]
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    expect(ui_page.locator(".ev.question")).to_contain_text(
        "Which path should I take?", timeout=10_000)
    expect(ui_page.locator(".ev.answer")).to_contain_text("answer (web): take B")
    expect(ui_page.locator(".ev.error")).to_contain_text("error (endpoint, attempt 1): boom")
    expect(ui_page.locator(".ev.compaction")).to_contain_text(
        "context compacted: 9000 \u2192 1000")
