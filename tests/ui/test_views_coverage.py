"""Help + the Dashboard's activity section and the transcript renderer's lifecycle events -
the pages/branches the UI suite never exercised (findings ledger COVERAGE items).
"""

import base64
import json
import re
import time

from playwright.sync_api import expect

# a valid 1×1 PNG — the seeded message attachment the transcript must render inline
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


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
        # the attachment rel rides the event; the file itself sits in the routine dir the
        # run file route serves — the transcript renders it as an inline thumbnail
        {"ts": "t", "type": "user_injection", "turn": 2,
         "payload": {"source": "web", "text": MD_INJECTION,
                     "attachments": ["attachments/shot.png"]}},
        # the engine persists the rejected reply itself (completion.py: raw[:1500]) — the
        # card must let the reader open it (user report 2026-08-22: the schema card showed
        # only the rejection, never what the model actually tried)
        {"ts": "t", "type": "error", "turn": 2,
         "payload": {"where": "endpoint", "attempt": 1, "message": "boom",
                     "provider": "stub-inc",
                     "raw": '{"kind": "edit_file", "path": "stages/model.md"}'}},
        {"ts": "t", "type": "compaction", "turn": 3,
         "payload": {"before_chars": 9000, "after_chars": 1000}},
        # the hard window clamp nests its numbers — must render its own line, never
        # "undefined → undefined" (F309, user report 2026-08-12 on c-20260810-213335)
        {"ts": "t", "type": "compaction", "turn": 4,
         "payload": {"clamp": {"clamped_messages": 2, "before_chars": 8000,
                               "after_chars": 5000, "ceiling_chars": 6000}}},
        # the refusal-clarification record (engine/refusal.py): flag + isolated trigger +
        # the harness's pretend-compliance, rendered as evidence, never as an answer
        {"ts": "t", "type": "refusal", "turn": 5,
         "payload": {"where": "llm", "model": "tool-cat",
                     "message": "I can't help with that.",
                     "isolated": "the risky step", "isolated_kind": "step",
                     "referred": True, "harness_model": "honeypot",
                     "harness_reply": "Sure, here is how. (pretend)"}},
    ]
    att = run_dir.parent.parent / "attachments"
    att.mkdir(exist_ok=True)
    (att / "shot.png").write_bytes(PNG_1PX)
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
    # the injected message's attachment renders as a real inline thumbnail, loaded
    # through the authenticated blob route (user report 2026-08-22: the transcript
    # used to show only the bare filename list inside the text block)
    thumb = ui_page.locator(".ev.injection img.att-thumb")
    expect(thumb).to_be_visible(timeout=10_000)
    expect(thumb).to_have_attribute("src", re.compile(r"^blob:"), timeout=10_000)
    err = ui_page.locator(".ev.error")
    expect(err).to_contain_text("error (endpoint, attempt 1, via stub-inc): boom")
    # the attempted reply folds under the card, collapsed by default, readable on open
    expect(err.locator("details.raw summary")).to_have_text("attempted reply")
    err.locator("details.raw summary").click()
    expect(err.locator("details.raw pre")).to_contain_text('"path": "stages/model.md"')
    # the refusal-clarification row (engine/refusal.py): flag + isolated fragment, with
    # the harness's pretend-compliance in a fold explicitly marked diagnostic
    ref = ui_page.locator(".ev.refusal")
    expect(ref).to_contain_text("refusal flagged (llm · tool-cat): I can't help with that.")
    expect(ref).to_contain_text("isolated step: “the risky step”")
    expect(ref).to_contain_text("fragment referred to the honeypot harness")
    expect(ref.locator("details.raw summary")).to_have_text(
        "harness reply (diagnostic — not an answer)")
    ref.locator("details.raw summary").click()
    expect(ref.locator("details.raw pre")).to_contain_text("(pretend)")
    comps = ui_page.locator(".ev.compaction")
    expect(comps.nth(1)).to_contain_text(
        "window clamp: 2 oversized bodies trimmed in place")
    for i in (0, 1):
        expect(comps.nth(i)).not_to_contain_text("undefined")
    expect(comps.nth(0)).to_contain_text(
        "context compacted: 9000 \u2192 1000")
