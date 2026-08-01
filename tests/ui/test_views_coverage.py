"""Help + the Dashboard's activity section and the transcript renderer's lifecycle events -
the pages/branches the UI suite never exercised (findings ledger COVERAGE items).
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


# A human message carrying every markdown construct the answer/injection bodies used to
# render literally: bold, a code span, and a list. Seeded into both transcript mounts.
MD_ANSWER = "take **B** \u2014 see `run.py` at https://example.com/docs\n\n- first\n- second"
MD_INJECTION = "look **deeper** at `engine/loop.py`"


def _seed_message_events(run_dir):
    """question / answer / error / compaction / user_injection, appended as the engine writes
    them. The answer and injection bodies carry markdown."""
    events = [
        {"ts": "t", "type": "question", "turn": 1,
         "payload": {"qid": "q1", "mode": "deferred", "type": "text",
                     "question": "Which path should I take?", "default": "A"}},
        {"ts": "t", "type": "answer", "turn": 1,
         "payload": {"qid": "q1", "source": "web", "text": MD_ANSWER}},
        {"ts": "t", "type": "user_injection", "turn": 2,
         "payload": {"source": "web", "text": MD_INJECTION}},
        {"ts": "t", "type": "error", "turn": 2,
         "payload": {"where": "endpoint", "attempt": 1, "message": "boom"}},
        {"ts": "t", "type": "compaction", "turn": 3,
         "payload": {"before_chars": 9000, "after_chars": 1000}},
    ]
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _expect_message_markdown(scope):
    """Both human-authored bodies rendered as markup, not as literal asterisks/backticks -
    the same assertion for whichever mount of createTranscript `scope` wraps."""
    answer = scope.locator(".ev.answer")
    expect(answer).to_contain_text("answer (web): take B", timeout=10_000)
    expect(answer.locator(".md strong")).to_have_text("B")
    expect(answer.locator(".md code")).to_have_text("run.py")
    expect(answer.locator(".md ul li")).to_have_count(2)
    # F228: a BARE http(s) URL in model/user prose autolinks to a new-tab anchor (not literal text)
    link = answer.locator('.md a[href="https://example.com/docs"]')
    expect(link).to_have_count(1)
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    injection = scope.locator(".ev.injection")
    expect(injection).to_contain_text("user: look deeper at")
    expect(injection.locator(".md strong")).to_have_text("deeper")
    expect(injection.locator(".md code")).to_have_text("engine/loop.py")


def test_dashboard_activity_section_lists_runs(ui, ui_page):
    """The cross-routine run feed (the whole of the former Log page) is the Dashboard's
    activity section: collapsed and inert until opened, then stats strip + feed. Expanding a
    row tails/replays that run's transcript inline - the capability the Log page carried."""
    # a RECENT ts - the feed default window is relative to now and hides old runs
    ts = time.strftime("%Y%m%d-070000")
    run_dir = ui.seed_run("uir", ts, "finished", summary="all done")
    _seed_message_events(run_dir)
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.wait_for_selector("details.activity-panel", timeout=10_000)
    ui_page.locator("details.activity-panel summary").click()
    expect(ui_page.locator(".activity-panel .stats .stat").first).to_be_visible(timeout=10_000)
    expect(ui_page.locator(".activity-panel .feed")).to_contain_text("uir", timeout=10_000)
    # inline transcript: the row expands into the run's own events
    ui_page.locator(".activity-panel .logrow .rowhead").first.click()
    expect(ui_page.locator(".activity-panel .logrow.open .logbody")).to_be_visible(timeout=10_000)
    # the SECOND mount of createTranscript: the markdown fix has to hold here too
    _expect_message_markdown(ui_page.locator(".activity-panel .logrow.open .logbody"))


def test_transcript_renders_lifecycle_events(ui, ui_page):
    """question / answer / error / compaction / user_injection transcript events all render as
    their own rows in the run view (the SIMPLE renderer map), and the two human-authored
    bodies render their markdown rather than showing it literally."""
    run_dir = ui.seed_run("uir", "20260714-070000", "finished", summary="done")
    _seed_message_events(run_dir)
    ui_page.goto(f"{ui.url}/#/run/uir:20260714-070000")
    expect(ui_page.locator(".ev.question")).to_contain_text(
        "Which path should I take?", timeout=10_000)
    _expect_message_markdown(ui_page)
    expect(ui_page.locator(".ev.error")).to_contain_text("error (endpoint, attempt 1): boom")
    expect(ui_page.locator(".ev.compaction")).to_contain_text(
        "context compacted: 9000 \u2192 1000")
