"""D308 — the operator starts a detached task from the composer and the rail shows it live, in
four claims against the real console: the "⇥ background" control posts the composer's text as a
task and the rail carries it at once WITHOUT starting a reply; a live row counts the time the
task has been running; the delivery arrives on the run's own bus event — no 15 s poll — showing
"delivered", the task's run page and the files that landed, each a link into the artifacts
panel; and the delivery renders as the hand-back card, which the chat has done for `via: branch`
since D306 while `via: background`, wired to the same card, was never loaded by any test.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import yaml
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .helpers import confirm_modal, start_conversation, watch_requests

ASK = "Keep an eye on the archive."
TASK_PROMPT = "crawl the whole archive\nand summarise every page"


def _ts(seconds_ago: int) -> str:
    """A run-ts `seconds_ago` in the past. Run-ts is ALWAYS UTC (ids.run_ts), and the console
    parses it as UTC — a local-time stamp here would read as an elapsed time off by the
    viewer's offset."""
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime("%Y%m%d-%H%M%S")


def _task(ui, taskid, owner, *, state, ts, delivered=False):
    """A detached task dir under background_home owned by `owner`, with one run."""
    d = ui.tmp / "background" / taskid
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": taskid, "name": "crawl the archive", "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin"},
        "workflow": {"library_slug": "general-task", "library_commit": ""},
        "owner": {"slug": owner, "dir": str(ui.conversations / owner)},
    }), encoding="utf-8")
    rd = d / "runs" / ts
    rd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(rd / "status.json",
                      {"run_id": f"{taskid}:{ts}", "state": state, "pid": 999999,
                       "summary": "crawled 200 pages"})
    if delivered:
        atomic_write_json(d / "delivered.json", {"ts": "t", "state": state, "owner": owner})
    return d


def _bus(page, event, run_id):
    """What the console's live stream dispatches for a run transition (static/app.js)."""
    page.evaluate("""([event, run_id]) => window.dispatchEvent(
        new CustomEvent('rsched-bus', {detail: {event, run_id, routine: run_id.split(':')[0]}}))""",
                  [event, run_id])


def test_the_composer_starts_a_detached_task_and_the_rail_carries_it_at_once(ui, ui_page):
    """D308 pt 3. The endpoint existed "so a human (or a test) can launch one directly" and
    nothing in static/ called it, so delegating a job meant spending a whole reply asking the
    assistant to detach one. The control takes the box as the prompt, offers its first line as
    the label, and starts NO reply — a person delegating a job is not a person asking the
    assistant something."""
    slug, _conv_dir = start_conversation(ui, ui_page, ASK)
    messages = watch_requests(ui_page, re.compile(r"/api/conversations/[^/]+/message"))
    ui_page.locator(".conv-composer textarea").fill(TASK_PROMPT)
    ui_page.get_by_role("button", name="⇥ background").click()

    # the one-field dialog, pre-filled with the prompt's first line
    label = ui_page.locator(".modal-overlay input")
    expect(label).to_have_value("crawl the whole archive")
    label.fill("archive crawl")
    confirm_modal(ui_page, "ok")

    card = ui_page.locator(".bg-task-card")
    expect(card).to_have_count(1)
    expect(card).to_contain_text("archive crawl")
    expect(card.locator(".chip")).to_have_text("queued")
    # the box is empty and NO reply was started: the text became the task
    expect(ui_page.locator(".conv-composer textarea")).to_have_value("")
    assert messages == [], messages
    # …and the server really has the intent
    reqs = sorted((ui.tmp / "background" / ".requests").glob("*.json"))
    assert len(reqs) == 1, reqs
    body = json.loads(reqs[0].read_text(encoding="utf-8"))
    # The route takes FORM fields, and `FormData` normalizes every string entry's line breaks
    # to CRLF before the body is built — so the prompt arrives with `\r\n`, exactly as the text
    # of an ordinary message does (the composer's send appends it the same way). Asserted as it
    # really lands rather than as it was typed: a test that papered over this would be claiming
    # a normalization that no code performs.
    assert body["prompt"] == TASK_PROMPT.replace("\n", "\r\n")
    assert body["label"] == "archive crawl"
    assert body["owner"]["slug"] == slug


def test_a_live_task_row_counts_the_time_it_has_been_running(ui, ui_page):
    """A row that says only "running" says nothing about a job that may have hours left. The
    count ticks, which is what distinguishes a live row from a stale render."""
    slug, _conv_dir = start_conversation(ui, ui_page, ASK)
    _task(ui, f"bg-{slug}-aaaa", slug, state="running", ts=_ts(12))
    ui_page.reload()

    elapsed = ui_page.locator(".bg-task-card .bg-task-elapsed")
    expect(elapsed).to_be_visible()
    first = elapsed.inner_text()
    assert re.fullmatch(r"\d+s", first), first
    ui_page.wait_for_timeout(1400)
    assert elapsed.inner_text() != first, f"the elapsed time is frozen at {first}"
    # a live task is cancellable and owes no delivery verdict yet
    expect(ui_page.locator(".bg-task-card .bg-cancel")).to_be_visible()
    expect(ui_page.locator(".bg-task-delivery")).to_have_count(0)


def test_a_delivery_reaches_the_rail_on_the_runs_own_event_with_its_files(ui, ui_page):
    """D308 pt 2, the whole claim in one pass: the refresh is driven by the task run's
    `run_finished` (the 15 s poll is gone), a terminal row says whether its result reached this
    conversation, it links to the task's own run page, and each landed file is a link into the
    artifacts panel rather than a name in prose."""
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    taskid = f"bg-{slug}-bbbb"
    ts = _ts(30)
    _task(ui, taskid, slug, state="running", ts=ts)
    ui_page.reload()
    expect(ui_page.locator(".bg-task-card .bg-task-elapsed")).to_be_visible()

    # the task finishes and the daemon hands its artefacts back
    _task(ui, taskid, slug, state="finished", ts=ts, delivered=True)
    landed = conv_dir / "artifacts" / f"from-bg-{taskid}"
    landed.mkdir(parents=True, exist_ok=True)
    (landed / "report.md").write_text("200 pages crawled", encoding="utf-8")
    _bus(ui_page, "run_finished", f"{taskid}:{ts}")

    card = ui_page.locator(".bg-task-card")
    expect(card.locator(".bg-task-delivery")).to_have_text("delivered")
    expect(card.locator(f'a[href="#/run/{taskid}:{ts}"]')).to_have_count(1)
    chip = card.locator(".attach-chip")
    expect(chip).to_have_count(1)
    expect(chip).to_have_attribute("title", f"artifacts/from-bg-{taskid}/report.md")
    chip.click()
    expect(ui_page.locator(".art-viewer")).to_contain_text("200 pages crawled")


def test_a_finished_task_that_has_not_handed_back_says_so(ui, ui_page):
    """`delivered` is about the HAND-BACK, not the run: the delivery is a later daemon tick, and
    one whose owner is gone is dropped. A row that said "finished" for both hid the only state
    the reader can act on."""
    slug, _conv_dir = start_conversation(ui, ui_page, ASK)
    _task(ui, f"bg-{slug}-cccc", slug, state="finished", ts=_ts(90))
    ui_page.reload()

    expect(ui_page.locator(".bg-task-delivery")).to_have_text("awaiting delivery")
    expect(ui_page.locator(".bg-task-card .attach-chip")).to_have_count(0)


#: header + the finished reply, so the transcript the chat renders is a complete one.
REPLY_EVENTS = [
    {"type": "header", "run_id": "X", "routine": "X",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "finish", "turns": 1, "usage_total": {"in": 18, "out": 2},
     "payload": {"status": "ok", "summary": "watching it."}},
]


def test_a_background_tasks_delivery_renders_as_the_hand_back_card(ui, ui_page):
    """The design says `via: background` reuses the card the branches order defines, and notes
    that no browser test covers that path — so the one channel people actually detach work on
    was the untested half. A delivery is a CHILD's result, never the person speaking."""
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    taskid = f"bg-{slug}-dddd"
    rel = f"artifacts/from-bg-{taskid}/report.md"
    (conv_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (conv_dir / rel).write_text("200 pages crawled", encoding="utf-8")
    handback = {"type": "user_injection", "ts": "2026-11-23T11:00:00+00:00",
                "payload": {"text": "[background task handed back] The task \u201ccrawl the "
                                    "archive\u201d finished.\n\n200 pages crawled, 3 dead links"
                                    "\n\nCollected into your artifacts/: " + rel,
                            "via": "background",
                            "handback": {"slug": taskid, "name": "crawl the archive",
                                         "paths": [rel]}}}
    run_dir = conv_dir / "runs" / "20261123-110000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in [*REPLY_EVENTS, handback]), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 1})
    ui_page.reload()

    card = ui_page.locator(".handback-card")
    expect(card).to_be_visible()
    # The LABEL line, not the card's text: the channel's hint sentence also contains the words
    # "background task", so asserting on the whole card passed even with the label naming the
    # wrong channel — measured against a neutered `CHANNELS.background.noun` this run.
    expect(card.locator(".evlabel")).to_contain_text("background task")
    expect(card.locator(".evlabel")).not_to_contain_text("branch")
    expect(card).to_contain_text("crawl the archive")
    expect(card).to_contain_text("200 pages crawled, 3 dead links")
    # a branch's card links to the branch's conversation; a task has none, so no dead link
    expect(card.locator("a")).to_have_count(0)
    # and it is NOT the operator's own words
    expect(ui_page.locator(".msg.user", has_text="handed back")).to_have_count(0)
    chip = card.locator(".attach-chip")
    expect(chip).to_have_count(1)
    chip.click()
    expect(ui_page.locator(".art-viewer")).to_contain_text("200 pages crawled")
