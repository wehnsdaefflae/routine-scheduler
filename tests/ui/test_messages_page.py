"""The Messages page (#/messages, the Items page renamed in D74): the system-maintenance
index — findings, decisions and reports as one filterable list, each card carrying its
status, origin and the changelog rows that addressed it, plus the whole self-audit inbox
queue ("waiting for the next run") and the note composer, whose message is a PLAIN user
message in the routine's inbox since D74 (no [AUDIT …] tag).
"""

import json
import re

from playwright.sync_api import expect

REPORT = {
    "schema": 1, "run_id": "self-audit:20260716-200000",
    "generated": "2026-07-16T20:00:00+00:00",
    "since": {"commit": "abc1234f", "window": "2 runs"},
    "summary": "F1 is carried this run; D1 awaits you.",
    "findings": [{"id": "F1", "severity": "problem", "title": "The thing is broken",
                  "detail": "Blocked on D1.", "evidence": ["src/rsched/engine/loop.py"]}],
    "decisions": [{"id": "D1", "status": "open", "title": "Pick a path",
                   "detail": "See F1 for the evidence.", "options": ["do it", "leave as-is"]}],
}

CHANGELOG = (
    '{"ts": "2026-07-15T09:00:00+00:00", "commit": "aaaa1111", "run_id": "self-audit:1", '
    '"summary": "0.90.0 \\u2014 F7 fixed the older thing"}\n'
    '{\n  "ts": "2026-07-16T09:00:00+00:00",\n  "commit": "bbbb2222",\n'
    '  "run_id": "self-audit:2",\n  "items": ["R1"],\n'
    '  "summary": "0.91.0 \\u2014 the bug report is fixed"\n}\n'
)

BUG = {"id": "R1", "ts": "2026-07-14T08:00:00+00:00", "routine": "uir",
       "run_id": "uir:20260714-070000", "title": "A util blew up under the sandbox",
       "detail": "exit 2 creating its output dir"}


def _seed(ui, make_routine, *, report=True):
    # a REAL self-audit routine: the page's generic message endpoints (create / edit /
    # withdraw, D74) resolve the slug through the registry, which needs routine.yaml
    make_routine(slug="self-audit")
    audit = ui.routines / "self-audit" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    if report:
        (audit / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    (audit / "changelog.jsonl").write_text(CHANGELOG, encoding="utf-8")
    control = ui.routines / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "reports.jsonl").write_text(json.dumps(BUG) + "\n", encoding="utf-8")


def test_messages_page_lists_every_type_with_status_and_history(ui, ui_page, make_routine):
    """All three item types land on one page: the report's finding and decision, the bug
    report from the .control stream, and the archive-only item that survives solely through
    the changelog. Each card shows a status; an archive-only card says so instead of
    inventing prose, and a prose-matched changelog link is labelled best-effort."""
    _seed(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/messages?status=all")
    ui_page.wait_for_selector("h1:has-text('Messages')", timeout=10_000)

    # the report header rides along (window + since-commit), the arrays are items now
    expect(ui_page.locator(".sub")).to_contain_text("findings, decisions and reports")
    expect(ui_page.locator("#ref-F1")).to_contain_text("The thing is broken")
    expect(ui_page.locator("#ref-F1")).to_contain_text("unknown")   # no status on disk yet
    expect(ui_page.locator("#ref-D1")).to_contain_text("Pick a path")
    expect(ui_page.locator("#ref-D1")).to_contain_text("open")
    expect(ui_page.locator("#ref-R1")).to_contain_text("A util blew up")
    expect(ui_page.locator("#ref-R1")).to_contain_text("report")
    expect(ui_page.locator("#ref-R1")).to_contain_text("addressed")  # explicit items: link

    # the archive-only item (F7 lives only in the changelog) carries no prose of its own
    expect(ui_page.locator("#ref-F7")).to_contain_text("archive")
    expect(ui_page.locator("#ref-F7")).to_contain_text("addressed")

    # an item's own history: R1's explicit link vs F7's prose match, labelled
    ui_page.locator("#ref-R1 details summary").click()
    expect(ui_page.locator("#ref-R1")).to_contain_text("linked")
    ui_page.locator("#ref-F7 details summary").click()
    expect(ui_page.locator("#ref-F7")).to_contain_text("best-effort")


def test_messages_filters_narrow_the_list_and_counts_stay_whole(ui, ui_page, make_routine):
    """Type / status / search filters run server-side; the chip counts stay over the
    UNFILTERED set so a chip never counts only what the current filter already shows."""
    _seed(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/messages?status=all")
    ui_page.wait_for_selector("#ref-F1", timeout=10_000)

    ui_page.locator(".filterbar .tag", has_text="reports").click()
    expect(ui_page.locator("#ref-R1")).to_be_visible()
    expect(ui_page.locator("#ref-F1")).to_have_count(0)
    expect(ui_page.locator(".filterbar .tag", has_text="findings")).to_contain_text("2")
    expect(ui_page).to_have_url(re.compile(r"type=report"))

    ui_page.locator(".filterbar .btn", has_text="clear").click()
    expect(ui_page.locator("#ref-F1")).to_be_visible(timeout=10_000)

    # search reaches the prose; an archive-only item is findable through its changelog summary
    ui_page.locator(".filterbar input[type=search]").fill("older thing")
    expect(ui_page.locator("#ref-F7")).to_be_visible(timeout=10_000)
    expect(ui_page.locator("#ref-F1")).to_have_count(0)


def test_messages_composer_queues_edits_and_withdraws_feedback(ui, ui_page, make_routine):
    """The reviewer-feedback loop: a comment on a finding lands in the self-audit inbox as
    a tagged message, shows up in "waiting for the next run", stays editable in place (same
    message file, structured audit channel), and withdraws through the generic messages
    endpoint (D74)."""
    _seed(ui, make_routine)
    inbox = ui.routines / "self-audit" / "inbox"
    ui_page.goto(f"{ui.url}/#/messages?status=all")
    ui_page.wait_for_selector("#ref-F1", timeout=10_000)

    ui_page.locator("#ref-F1 textarea").fill("please fix this first")
    ui_page.locator("#ref-F1 button", has_text="send comment").click()

    pending = ui_page.locator(".pending-item", has_text="please fix this first")
    expect(pending).to_be_visible(timeout=10_000)
    msgs = list(inbox.glob("msg-*.json"))
    assert len(msgs) == 1
    assert json.loads(msgs[0].read_text())["text"] == \
        "[AUDIT feedback · finding F1] please fix this first"

    # the queued comment rides back into the finding's own box, editable in place
    expect(ui_page.locator("#ref-F1 textarea")).to_have_value("please fix this first")
    ui_page.locator("#ref-F1 textarea").fill("actually, do this instead")
    ui_page.locator("#ref-F1 button", has_text="update comment").click()
    expect(ui_page.locator(".pending-item", has_text="actually, do this instead")).to_be_visible(
        timeout=10_000)
    assert len(list(inbox.glob("msg-*.json"))) == 1        # SAME file, not a second message

    ui_page.locator("#ref-F1 button", has_text="withdraw").click()
    expect(ui_page.locator(".pending-item")).to_have_count(0, timeout=10_000)
    assert list(inbox.glob("msg-*.json")) == []


def test_messages_note_is_a_plain_inbox_message(ui, ui_page, make_routine):
    """The note for the next run (D74 phase 4): the composer creates a PLAIN user message
    in self-audit's inbox — no [AUDIT note] wrapper — through the same generic endpoint
    every routine page uses; it queues on the waiting list, is editable there in place
    (same file), and withdrawable."""
    _seed(ui, make_routine)
    inbox = ui.routines / "self-audit" / "inbox"
    ui_page.goto(f"{ui.url}/#/messages?status=all")
    ui_page.wait_for_selector("h2:has-text('Message the next run')", timeout=10_000)
    ui_page.locator("textarea.code").fill("focus on the daemon logging")
    ui_page.locator("button", has_text="send to the next run").click()

    row = ui_page.locator(".pending-item", has_text="focus on the daemon logging")
    expect(row).to_be_visible(timeout=10_000)
    msgs = list(inbox.glob("msg-*.json"))
    assert len(msgs) == 1
    assert json.loads(msgs[0].read_text())["text"] == "focus on the daemon logging"

    # a plain queued message is editable right on the waiting list — same file, new text
    row.locator("button", has_text="edit").click()
    edit_box = ui_page.locator(".pending-item textarea")
    expect(edit_box).to_have_value("focus on the daemon logging")
    edit_box.fill("focus on the scheduler instead")
    ui_page.locator(".pending-item button", has_text="save").click()
    expect(ui_page.locator(".pending-item", has_text="focus on the scheduler instead")
           ).to_be_visible(timeout=10_000)
    msgs = list(inbox.glob("msg-*.json"))
    assert len(msgs) == 1
    assert json.loads(msgs[0].read_text())["text"] == "focus on the scheduler instead"

    ui_page.locator(".pending-item button", has_text="withdraw").click()
    expect(ui_page.locator(".pending-item")).to_have_count(0, timeout=10_000)
    assert list(inbox.glob("msg-*.json")) == []


def test_messages_without_a_report_still_lists_the_archive(ui, ui_page, make_routine):
    """A routine that never produced a report is not an empty page: the changelog archive and
    the bug stream are items in their own right, and the note box stays available."""
    _seed(ui, make_routine, report=False)
    ui_page.goto(f"{ui.url}/#/messages?status=all")
    ui_page.wait_for_selector("h1:has-text('Messages')", timeout=10_000)
    expect(ui_page.locator("#ref-R1")).to_be_visible(timeout=10_000)
    expect(ui_page.locator("#ref-F7")).to_be_visible()
    expect(ui_page.locator("h2", has_text="Message the next run")).to_be_visible()


def test_messages_empty_state_without_the_self_audit_routine(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/messages?status=all")
    expect(ui_page.locator(".empty .t")).to_contain_text("isn't set up yet", timeout=10_000)


def test_messages_defaults_to_the_active_backlog(ui, ui_page, make_routine):
    """A bare #/messages shows only open + in_progress (the worklist), with the `active`
    chip lit — the archive (addressed/settled/unknown) needs the explicit ?status=all
    (D75)."""
    _seed(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/messages")
    ui_page.wait_for_selector("h1:has-text('Messages')", timeout=10_000)
    expect(ui_page.locator("#ref-D1")).to_be_visible(timeout=10_000)   # open → shown
    expect(ui_page.locator("#ref-R1")).to_have_count(0)                # addressed → hidden
    expect(ui_page.locator("#ref-F1")).to_have_count(0)                # unknown → hidden
    expect(ui_page.locator(".filterbar .tag.on", has_text="active")).to_be_visible()
    # clearing the filters is the explicit "show everything" and survives as ?status=all
    ui_page.locator(".filterbar .btn", has_text="clear").click()
    expect(ui_page.locator("#ref-R1")).to_be_visible(timeout=10_000)
    expect(ui_page).to_have_url(re.compile(r"status=all"))


def test_messages_priority_flag_round_trips(ui, ui_page, make_routine):
    """The ⚑ toggle (D75): flagging a card floats it, badges it, and lands in the
    .control/item-priorities.json store the owning routine's next run reads; unflagging
    clears the store again."""
    _seed(ui, make_routine)
    ui_page.goto(f"{ui.url}/#/messages")
    ui_page.wait_for_selector("#ref-D1", timeout=10_000)
    ui_page.locator("#ref-D1 button[title*='flag as priority']").click()
    expect(ui_page.locator("#ref-D1")).to_contain_text("⚑ priority", timeout=10_000)
    store = ui.routines / ".control" / "item-priorities.json"
    assert "D1" in json.loads(store.read_text(encoding="utf-8"))
    ui_page.locator("#ref-D1 button[title*='unflag']").click()
    expect(ui_page.locator("#ref-D1 button[title*='flag as priority']")).to_be_visible(
        timeout=10_000)
    assert json.loads(store.read_text(encoding="utf-8")) == {}
