"""The four safety-net flows (improvement-plan item 3), driven end-to-end in a real
browser against the real app: Decisions answering, the conversation composer, routine-page
saves, and Settings endpoints CRUD. Assertions check BOTH what the user sees (DOM, toast)
and what actually landed on disk — the UI lying about a save is exactly the bug class this
harness exists to catch.
"""

import json
import time

import yaml
from playwright.sync_api import expect


def _toast(page):
    return page.locator("#toast:not([hidden])")


def _wait_until(cond, timeout_s=8.0):
    """Explicit persist-wait: poll a condition instead of sleeping a fixed amount — fixed
    sleeps before disk asserts are exactly what flakes under xdist load (standing rule,
    self-audit 2026-07-17)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"condition not met within {timeout_s:.1f}s")


# ---- 1. Decisions answer flow ------------------------------------------------------------


def test_decisions_answer_flow(ui, ui_page):
    ui.seed_question("uir", "q-color", "Which color should the report use?",
                     options=["red", "blue"], default="red")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Which color should the report use?")
    expect(card).to_contain_text("without an answer: red")

    # F189: clicking an option button SUBMITS that option one-click (free-text answering
    # stays possible — covered by the blocking-question flow below)
    card.get_by_role("button", name="1 · red").click()
    expect(_toast(ui_page)).to_contain_text("answered")
    expect(card.locator(".chip.ok")).to_contain_text("answered · queued")
    answer = json.loads(
        (ui.routine_dir("uir") / "inbox" / "answer-q-color.json").read_text(encoding="utf-8"))
    assert answer["text"] == "red"
    assert answer["source"] == "web"


def test_decisions_blocking_question_from_live_run(ui, ui_page):
    ui.seed_run("uir", "20260715-090000", "waiting_user",
                question={"qid": "q-go", "question": "Ship it?", "options": [],
                          "default": "", "asked": "20260715-090000"})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item.warn")   # blocking questions render loud
    expect(card).to_contain_text("Ship it?")
    card.locator('textarea[data-persist="answer-q-go"]').fill("yes — ship")
    card.get_by_role("button", name="answer").click()
    expect(_toast(ui_page)).to_contain_text("answered — the run resumes")
    answer = json.loads(
        (ui.routine_dir("uir") / "inbox" / "answer-q-go.json").read_text(encoding="utf-8"))
    assert answer["text"] == "yes — ship"


def test_decisions_snooze_and_defer(ui, ui_page):
    ui.seed_question("uir", "q-snz", "Review the weekly digest?")
    ui.seed_run("uir", "20260715-090000", "waiting_user",
                question={"qid": "q-blk", "question": "Overwrite the export?", "options": [],
                          "default": "keep both", "asked": "20260715-090000"})
    ui.seed_question("uir", "q-blk", "Overwrite the export?", mode="blocking",
                     default="keep both")
    ui_page.goto(f"{ui.url}/#/questions")

    # snooze the deferred one → it leaves the inbox and waits under the Snoozed filter
    card = ui_page.locator(".question-item", has_text="weekly digest")
    card.locator("select").select_option("60")
    expect(_toast(ui_page)).to_contain_text("snoozed")
    expect(ui_page.locator(".question-item", has_text="weekly digest")).to_have_count(0)
    record = json.loads((ui.routine_dir("uir") / "questions" / "pending" / "q-snz.json")
                        .read_text(encoding="utf-8"))
    assert record["snoozed_until"]
    ui_page.get_by_role("button", name="Snoozed · 1").click()
    snoozed = ui_page.locator(".question-item", has_text="weekly digest")
    expect(snoozed.locator(".chip.meta", has_text="snoozed")).to_be_visible()
    snoozed.get_by_role("button", name="unsnooze").click()
    expect(_toast(ui_page)).to_contain_text("back in the inbox")

    # defer the blocking one → the release marker lands in the inbox, the card settles
    ui_page.get_by_role("button", name="All · 2").click()
    blocking = ui_page.locator(".question-item", has_text="Overwrite the export?")
    blocking.get_by_role("button", name="defer to next run").click()
    expect(_toast(ui_page)).to_contain_text("deferred")
    marker = json.loads((ui.routine_dir("uir") / "inbox" / "answer-q-blk.json")
                        .read_text(encoding="utf-8"))
    assert marker["defer"] is True


def test_decisions_inbox_groups(ui, ui_page):
    """Priority view renders SECTIONS (blocking > deferred), an about-to-expire blocking
    ask carries the loud chip, and keyboard focus lands on the first (most urgent) input."""
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(minutes=10)).isoformat(timespec="seconds")
    ui.seed_question("uir", "q-d1", "Deferred thing?")
    ui.seed_run("uir", "20260715-090000", "waiting_user",
                question={"qid": "q-b1", "question": "Blocking thing?", "options": [],
                          "asked": "20260715-090000", "expires": soon})
    ui.seed_question("uir", "q-b1", "Blocking thing?", mode="blocking", expires=soon)
    ui_page.goto(f"{ui.url}/#/questions")

    heads = ui_page.locator(".q-group-head")
    expect(heads).to_have_count(2)
    expect(heads.nth(0)).to_contain_text("Blocking")
    expect(heads.nth(1)).to_contain_text("Deferred")
    expect(ui_page.locator(".question-item.warn .chip", has_text="expiring")).to_be_visible()
    assert ui_page.evaluate("document.activeElement.dataset.persist") == "answer-q-b1"


def test_state_graph_shows_phase_instrumentation(ui, ui_page):
    """The run view's state-graph rail shows per-phase turns/tokens/time from the
    transcript — the instrument panel, not just a highlighted chain. The current phase
    is the run's recorded one (status.json), appended as its own node when the routine
    has no matching stage module."""
    run_dir = ui.seed_run("uir", "20260715-130000", "finished", summary="done", phase="only")
    events = [
        {"ts": "2026-07-15T10:00:00+00:00", "type": "header",
         "payload": {}, "run_id": "uir:20260715-130000"},
        {"ts": "2026-07-15T10:00:30+00:00", "type": "assistant_action", "phase": "only",
         "usage": {"in": 900, "out": 100}, "turn": 1, "payload": {"kind": "util", "say": "x"}},
        {"ts": "2026-07-15T10:02:00+00:00", "type": "assistant_action", "phase": "only",
         "usage": {"in": 500, "out": 100}, "turn": 2,
         "payload": {"kind": "finish", "say": "done", "status": "ok", "summary": "done"}},
    ]
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/run/uir:20260715-130000")
    node = ui_page.locator(".sg-node", has_text="only")
    expect(node.locator(".sg-stats")).to_contain_text("2 turns")
    expect(node.locator(".sg-stats")).to_contain_text("1.6k tok")
    # AUDIT note: the turn count and timestamp stack VERTICALLY (.turnmeta) — the timestamp
    # sits under the turn count, not beside it, reclaiming horizontal space for the say text.
    meta = ui_page.locator(".turn .say .turnmeta").first
    expect(meta.locator(".n")).to_contain_text("turn 1")
    expect(meta.locator(".ts")).to_be_visible()


def test_state_graph_marks_skipped_phases(ui, ui_page):
    """A stage the run jumped over (no turn ever ran under its module) reads 'skipped',
    not checked-off — the diagram never claims work that didn't happen. Nodes come from
    the routine's stage modules, in main.md mention order."""
    run_dir = ui.seed_run("uir", "20260715-133000", "running", phase="act")
    stages = ui.routine_dir("uir") / "stages"
    stages.mkdir(exist_ok=True)
    for name in ("gather", "analyse", "act"):
        (stages / f"{name}.md").write_text(f"# Step: {name}\n", encoding="utf-8")
    (ui.routine_dir("uir") / "main.md").write_text(
        "## Run flow\n1. `stages/gather.md` — g.\n2. `stages/analyse.md` — a.\n"
        "3. `stages/act.md` — x.\n", encoding="utf-8")
    events = [
        {"ts": "2026-07-15T10:00:30+00:00", "type": "assistant_action", "phase": "gather",
         "usage": {"in": 900, "out": 100}, "turn": 1, "payload": {"kind": "util", "say": "x"}},
        {"ts": "2026-07-15T10:01:00+00:00", "type": "assistant_action", "phase": "act",
         "usage": {"in": 500, "out": 100}, "turn": 2, "payload": {"kind": "util", "say": "y"}},
    ]
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/run/uir:20260715-133000")
    expect(ui_page.locator(".sg-node", has_text="gather")).to_have_class("sg-node done")
    skipped = ui_page.locator(".sg-node", has_text="analyse")
    expect(skipped).to_have_class("sg-node done skipped")
    expect(skipped.locator(".sg-stats")).to_have_text("skipped")


def test_run_rail_lists_file_activity(ui, ui_page):
    """The run rail's files card answers 'what did this run read and write' at a glance —
    per-path counts from the transcript's observations, failed touches flagged."""
    run_dir = ui.seed_run("uir", "20260715-140000", "finished", summary="done")
    events = [
        {"type": "observation", "turn": 1, "payload": {
            "kind": "read_file", "path": "state/notes.md", "content": "x"}},
        {"type": "observation", "turn": 2, "payload": {
            "kind": "read_file", "path": "state/notes.md", "content": "x"}},
        {"type": "observation", "turn": 3, "payload": {
            "kind": "write_file", "path": "artifacts/report.html", "bytes": 42}},
        {"type": "observation", "turn": 4, "payload": {
            "kind": "edit_file", "path": "routine.yaml", "error": "never writable"}},
    ]
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(e) + "\n" for e in events)
    (run_dir / "history").mkdir()
    (run_dir / "history" / "notes-001.md").write_text("archived text", encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/run/uir:20260715-140000")
    rows = ui_page.locator(".file-row")
    expect(rows).to_have_count(4)   # 3 touched files + 1 compacted-history row
    expect(rows.nth(0)).to_contain_text("state/notes.md")
    expect(rows.nth(0).locator(".file-ops")).to_have_text("read ×2")
    expect(rows.nth(1).locator(".file-ops")).to_have_text("wrote")
    expect(rows.nth(2)).to_have_class("file-row err")
    expect(rows.nth(2).locator(".file-ops")).to_have_text("✕1")
    # every row carries view + download affordances (user order 2026-08-12), and the
    # compaction archive is listed as servable rows under its own sub-head
    expect(rows.nth(0).locator(".file-act")).to_have_count(2)
    expect(ui_page.locator(".filelist .rail-sub")).to_have_text("compacted history")
    hist = ui_page.locator(".file-row.hist")
    expect(hist).to_have_count(1)
    expect(hist).to_contain_text("notes-001.md")
    expect(hist.locator(".file-act")).to_have_count(2)


def test_run_view_plan_strip(ui, ui_page):
    """D54: the run view shows the run's WORKING PLAN (state/plan.md) as an always-visible
    strip, rendered as markdown — so 'where is this run in its own plan' is answerable at a
    glance. The strip reuses the same store the engine inlines into the prompt."""
    ui.seed_run("uir", "20260731-090000", "running")
    (ui.routine_dir("uir") / "state").mkdir(parents=True, exist_ok=True)
    (ui.routine_dir("uir") / "state" / "plan.md").write_text(
        "1. [x] gather evidence\n2. [ ] **build the plan strip** — HERE\n3. [ ] write report\n",
        encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:20260731-090000")
    strip = ui_page.locator(".plan-strip")
    expect(strip).to_be_visible()
    expect(strip.locator("summary")).to_contain_text("working plan")
    expect(strip.locator(".plan-body")).to_contain_text("build the plan strip")
    # rendered AS markdown (the sanctioned md() path), not raw text
    expect(strip.locator(".plan-body strong")).to_contain_text("build the plan strip")


def test_run_view_plan_strip_hidden_without_plan(ui, ui_page):
    """A run that keeps no plan (a scheduled routine whose spine is its recipe) shows no
    strip — it takes no space rather than rendering an empty box."""
    ui.seed_run("uir", "20260731-093000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260731-093000")
    # the transcript rendering means the view has booted; the strip stays hidden
    expect(ui_page.locator(".runbar")).to_be_visible()
    expect(ui_page.locator(".plan-strip")).to_be_hidden()


def test_run_view_question_form(ui, ui_page):
    """The run view's blocking-question panel rides the shared answerForm: the mirrored/
    Discord note renders, ask-back sends an intermediate reply, and clicking an option
    SUBMITS it one-click (F189) — no prefill-then-Enter second step."""
    ui.seed_run("uir", "20260715-100000", "waiting_user",
                question={"qid": "q-rv", "question": "Which path?", "options": ["a", "b"],
                          "default": "a", "expires": "2026-07-15T13:00:00+00:00",
                          "mirrored": True, "asked": "20260715-100000"})
    ui.seed_question("uir", "q-rv", "Which path?", mode="blocking", default="a")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-100000")
    box = ui_page.locator(".panel.warn", has_text="Which path?")
    expect(box).to_contain_text("and on Discord")
    expect(box).to_contain_text("without an answer: a")
    box.locator("textarea").fill("thinking out loud: why not both?")
    box.get_by_role("button", name="ask back").click()
    expect(_toast(ui_page)).to_contain_text("the model will reply and re-ask")
    answer = json.loads(
        (ui.routine_dir("uir") / "inbox" / "answer-q-rv.json").read_text(encoding="utf-8"))
    assert answer["intermediate"] is True
    assert answer["text"] == "thinking out loud: why not both?"
    # one-click option submit on this panel is covered by
    # test_run_page_blocking_question_shows_option_buttons (F189)


def test_answering_an_already_resolved_question_settles_gently(ui, ui_page):
    """F259: a question answered on one surface (or expired) leaves stale answer cards on
    other open surfaces still showing an actionable button. Clicking it hits the backend's
    404 `no open question` — the benign 'already resolved elsewhere' end-state. The shared
    answerForm must settle the card with a plain notice, NOT a red error toast (which also
    logs a UI-friction trace event) plus re-enabled buttons inviting a doomed retry. Here the
    answer POST is routed to that 404 and we require the toast is the benign notice, not `.err`."""
    import re

    ui.seed_run("uir", "20260716-100000", "waiting_user",
                question={"qid": "q-stale", "question": "Which path?", "options": ["a", "b"],
                          "default": "a", "asked": "20260716-100000"})
    ui.seed_question("uir", "q-stale", "Which path?", mode="blocking", default="a")

    def handle(route):
        route.fulfill(status=404, content_type="application/json",
                      body=json.dumps({"detail": "no open question 'q-stale'"}))
    ui_page.route("**/api/questions/q-stale/answer", handle)

    ui_page.goto(f"{ui.url}/#/run/uir:20260716-100000")
    box = ui_page.locator(".panel.warn", has_text="Which path?")
    box.get_by_role("button", name="a", exact=True).click()   # one-click option submit
    # the benign notice appears and it is NOT an error toast
    toast = _toast(ui_page)
    expect(toast).to_contain_text("already answered elsewhere")
    expect(toast).not_to_have_class(re.compile(r"\berr\b"))
    # the card settled (the host cleared it) — the actionable option button is gone
    expect(box.get_by_role("button", name="a", exact=True)).to_have_count(0)


def test_long_option_label_does_not_overflow(ui, ui_page):
    """A decision option can be a full sentence. The option button must wrap and stay
    within the question card width instead of overflowing right on a narrow viewport
    (F80). Guards the .answer-opts .btn { white-space: normal; max-width: 100% } rule."""
    long_opt = ("B: promote clarify sessions to real runs of the clarification routine so "
                "their ids are valid with no addressing bridge required")
    ui_page.set_viewport_size({"width": 400, "height": 900})
    ui.seed_question("uir", "q-long", "Which addressing bridge?",
                     options=[long_opt, "leave as-is"], default="leave as-is")
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item")
    expect(card).to_be_visible()
    btn = card.get_by_role("button", name=f"1 · {long_opt}", exact=True)
    expect(btn).to_be_visible()
    card_box = card.bounding_box()
    btn_box = btn.bounding_box()
    # the button's right edge must not extend past the card's right edge (+1px slack)
    assert btn_box["x"] + btn_box["width"] <= card_box["x"] + card_box["width"] + 1, (
        f"option button overflows card: btn right={btn_box['x'] + btn_box['width']}, "
        f"card right={card_box['x'] + card_box['width']}")


def test_run_view_message_modes(ui, ui_page):
    """ONE input with an explicit mode: a live run fixes it to inject; a terminal run
    ALWAYS continues THIS run (F233 removed the queue-for-next-run mode — that moved to the
    routine details page)."""
    ui.seed_run("uir", "20260715-110000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-110000")
    # F237: no mode <select> — a live run's input injects (shown by its placeholder), a
    # terminal run's input continues the run; the destination is implied by run state.
    assert ui_page.locator('select[title="where this message goes"]').count() == 0
    ui_page.locator('textarea[placeholder="inject a message into the run…"]').fill("mid-run note")
    # attachments ride a run message too (F202): picking a file shows a chip; the send
    # stores it under the routine's attachments/ and the inbox message records the rel
    ui_page.locator('input[type="file"]').set_input_files(
        {"name": "shot.png", "mimeType": "image/png", "buffer": b"\x89PNG fake"})
    expect(ui_page.locator(".attach-chip")).to_contain_text("shot.png")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_be_visible()
    inbox = ui.routine_dir("uir") / "inbox"
    assert any("mid-run note" in m.read_text(encoding="utf-8")
               for m in inbox.glob("msg-*.json"))
    msg = next(json.loads(m.read_text(encoding="utf-8")) for m in inbox.glob("msg-*.json")
               if "mid-run note" in m.read_text(encoding="utf-8"))
    assert msg["attachments"] and msg["attachments"][0].startswith("attachments/")
    assert (ui.routine_dir("uir") / msg["attachments"][0]).is_file()
    expect(ui_page.locator(".attach-chip")).to_have_count(0)   # chips clear after send

    ui.seed_run("uir", "20260715-120000", "finished", summary="done")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-120000")
    # F237: a terminal run's input ALWAYS continues this run, so the vestigial single-option
    # mode <select> is gone entirely — the composer is just the input + send. (F233 had
    # already removed the "queue for next run" option, leaving a dead disabled dropdown.)
    assert ui_page.locator('select[title="where this message goes"]').count() == 0
    # F238: on a narrow screen the message input takes its OWN full-width line and the
    # send button wraps BENEATH it — not squished inline beside the controls. This asserts
    # the real layout (not just the class): the earlier count-only check passed even while an
    # inline flex:1 on the input silently beat the ≤860px stylesheet rule and kept it inline.
    composer_input = ui_page.locator("div.composer textarea[data-persist='run-msg']")
    assert composer_input.count() == 1
    ui_page.set_viewport_size({"width": 400, "height": 900})
    row = ui_page.locator("div.composer")
    send = ui_page.locator("div.composer").get_by_role("button", name="send", exact=True)
    rbox = row.bounding_box()
    ibox = composer_input.bounding_box()
    sbox = send.bounding_box()
    # the input spans (near) the full composer width — its own line, not sharing it
    assert ibox["width"] >= rbox["width"] * 0.9, (
        f"composer input not full-width on narrow screen: input {ibox['width']}px "
        f"of row {rbox['width']}px")
    # …and the send button sits on a line BELOW the input (wrapped), not beside it
    assert sbox["y"] >= ibox["y"] + ibox["height"] - 1, (
        f"send button did not wrap below the input: input bottom {ibox['y'] + ibox['height']}, "
        f"button top {sbox['y']}")
    ui_page.set_viewport_size({"width": 1280, "height": 900})   # restore for the rest
    ui_page.locator('textarea[placeholder^="message…"]').fill("continue please")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_contain_text("continue the conversation")
    assert any("continue please" in m.read_text(encoding="utf-8")
               for m in inbox.glob("msg-*.json"))


def test_run_view_composer_draft_persists_and_clears_on_send(ui, ui_page):
    """F215: the run composer input has a STABLE persist key, so a typed draft survives a
    page refresh (formpersist), and it CLEARS on send instead of leaving the sent text
    behind."""
    ui.seed_run("uir", "20260729-140000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260729-140000")
    composer = ui_page.locator('textarea[data-persist="run-msg"]')
    expect(composer).to_be_visible()
    composer.fill("a draft I am still writing")
    # refresh — the draft must come back (was broken: placeholder-keyed draft lost when the
    # placeholder changed)
    ui_page.reload()
    composer = ui_page.locator('textarea[data-persist="run-msg"]')
    expect(composer).to_have_value("a draft I am still writing")
    # sending clears the input
    composer.fill("mid-run note to send")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_be_visible()
    expect(composer).to_have_value("")


def test_conversation_admin_toggle_sends_token(ui, ui_page):
    """D63-1A: the Conversations composer has an Admin toggle. Off by default; clicking it
    prompts for the admin token (themed dialog), stores it for THIS browser session, and reads
    'admin: on'. Every message it then sends carries the x-admin-token header (the server
    re-checks it and drops the one-shot admin marker). Toggling off forgets the token and the
    next message carries no admin header."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("plan the week with the full toolset")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")

    admin = ui_page.get_by_role("button", name="admin", exact=True)
    admin.wait_for(timeout=10_000)                      # the composer mounts after an async fetch
    expect(admin).to_be_visible()                       # off by default: label is plain "admin"
    admin.click()
    # the themed prompt collects the token (no native prompt) — fill it and confirm
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill("s3cret-admin-token")
    dlg.get_by_role("button", name="ok").click()
    expect(ui_page.get_by_role("button", name="admin: on")).to_be_visible()

    # sending now carries the admin header
    with ui_page.expect_request("**/api/conversations/**/message") as req:
        ui_page.locator(".conv-composer textarea").fill("do the thing")
        ui_page.get_by_role("button", name="send", exact=True).click()
    assert req.value.headers.get("x-admin-token") == "s3cret-admin-token"

    # toggle off → the label reverts and the next send carries NO admin header
    ui_page.get_by_role("button", name="admin: on").click()
    expect(ui_page.get_by_role("button", name="admin", exact=True)).to_be_visible()
    with ui_page.expect_request("**/api/conversations/**/message") as req2:
        ui_page.locator(".conv-composer textarea").fill("and again")
        ui_page.get_by_role("button", name="send", exact=True).click()
    assert req2.value.headers.get("x-admin-token") is None


def test_new_conversation_admin_toggle_sends_token_on_create(ui, ui_page):
    """D66: the NEW-conversation composer also has an Admin toggle, because reply #1 fires
    on create — arming admin only AFTER create would miss the first reply. Arming it here and
    starting the conversation makes the CREATE request (POST /api/conversations) carry the
    x-admin-token header; an unarmed create carries none."""
    ui_page.goto(f"{ui.url}/#/conversations")
    admin = ui_page.get_by_role("button", name="admin", exact=True)
    admin.wait_for(timeout=10_000)                      # composer mounts after an async fetch
    expect(admin).to_be_visible()                       # off by default
    admin.click()
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill("s3cret-admin-token")
    dlg.get_by_role("button", name="ok").click()
    expect(ui_page.get_by_role("button", name="admin: on")).to_be_visible()

    # starting the conversation now carries the admin header on the CREATE request
    ui_page.locator(".conv-new textarea").fill("plan the week with the full toolset")
    with ui_page.expect_request(
            lambda r: r.url.rstrip("/").endswith("/api/conversations")
            and r.method == "POST") as req:
        ui_page.get_by_role("button", name="start conversation").click()
    assert req.value.headers.get("x-admin-token") == "s3cret-admin-token"


def test_run_view_recipe_edit_checkbox(ui, ui_page):
    """D37 (revised): a terminal routine run shows the "editable recipe" CHECKBOX right
    next to the composer input — OFF by default; checking it flips the placeholder to say
    the continuation may edit the recipe files. A live run hides it."""
    ui.seed_run("uir", "20260723-090000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260723-090000")
    assert ui_page.locator('select[title="where this message goes"]').count() == 0  # F237
    expect(ui_page.get_by_label("editable recipe")).to_be_hidden()

    ui.seed_run("uir", "20260723-100000", "finished", summary="done")
    ui_page.goto(f"{ui.url}/#/run/uir:20260723-100000")
    chk = ui_page.get_by_label("editable recipe")
    expect(chk).to_be_visible()
    expect(chk).not_to_be_checked()                     # off by default
    chk.check()
    expect(ui_page.locator('textarea[placeholder*="may edit the routine"]')).to_be_visible()
    chk.uncheck()
    expect(ui_page.locator(
        'textarea[placeholder="message… (continues this run)"]')).to_be_visible()


def test_run_view_deliberation_relevel(ui, ui_page):
    """The run view's ⚙ deliberation control re-levels a LIVE run: one arrow key on the
    slider posts to /runs/{id}/deliberation and the signal lands in control.json
    (run-scoped, applied by the engine at the next turn boundary)."""
    run_dir = ui.seed_run("uir", "20260716-090000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260716-090000")
    ui_page.locator("details", has_text="⚙ deliberation").locator("summary").click()
    slider = ui_page.locator('.delib input[type="range"]')
    slider.focus()
    slider.press("ArrowRight")                        # standard → deliberate
    expect(_toast(ui_page)).to_contain_text("takes effect next turn")
    ctrl = json.loads((run_dir / "control.json").read_text(encoding="utf-8"))
    assert ctrl["set_deliberation"]["level"] == "deliberate"
    assert ctrl["set_deliberation"]["ts"]


def test_artifact_row_shows_time_and_deletes(ui, ui_page):
    """The artifact row shows WHEN the file was last updated (user order 2026-08-14 —
    an artifact is rewritten in place, so the version must be visible, not a tooltip)
    and its hover delete removes the file after the confirm dialog."""
    ui.seed_run("uir", "20260715-150000", "finished", summary="done")
    art = ui.routine_dir("uir") / "artifacts"
    art.mkdir(exist_ok=True)
    (art / "notes.md").write_text("# n", encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-150000")
    row = ui_page.locator(".art-item")
    expect(row).to_have_count(1)
    expect(row.locator(".art-time")).not_to_be_empty()
    ui_page.on("dialog", lambda d: d.accept())
    row.hover()
    row.locator(".art-del").click()
    expect(ui_page.locator(".art-item")).to_have_count(0)
    assert not (art / "notes.md").exists()


def test_run_transcript_story_and_refer(ui, ui_page):
    """The transcript reads as a story: a phase change draws a labeled divider, an injected
    message's leading `> re …` line renders as a quote chip, and the ↩ on a turn primes the
    composer — the sent text leads with the quoted reference and the chip clears."""
    run_dir = ui.seed_run("uir", "20260715-150000", "finished", summary="done")
    events = [
        {"ts": "2026-07-15T10:00:30+00:00", "type": "assistant_action", "phase": "gather",
         "turn": 1, "usage": {"in": 10, "out": 5},
         "payload": {"kind": "util", "name": "websearch", "args": ["llm jobs"],
                     "say": "Catalog fits — scanning portals.",
                     "note": "portal 1 needs the site: filter — plain queries return noise"}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "util", "name": "websearch", "exit": 0, "stdout": "3 hits",
                     "truncated": True,
                     "full_output": {"stdout": ".util_outputs/20260715-150000/t1-websearch.out",
                                     "stdout_chars": 41234}}},
        {"ts": "2026-07-15T10:01:30+00:00", "type": "assistant_action", "phase": "report",
         "turn": 2, "usage": {"in": 10, "out": 5},
         "payload": {"kind": "write_file", "path": "artifacts/r.md",
                     "say": "Hits are solid — writing the report."}},
        {"type": "user_injection",
         "payload": {"text": "> re turn 1 (util websearch): Catalog fits — scanning "
                             "portals.\n\nlook deeper"}},
    ]
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(e) + "\n" for e in events)

    ui_page.goto(f"{ui.url}/#/run/uir:20260715-150000")
    dividers = ui_page.locator(".phase-divider")
    expect(dividers).to_have_count(2)
    expect(dividers.nth(0)).to_have_text("gather")
    expect(dividers.nth(1)).to_have_text("report")
    # a captured note renders as its own 📌 line inside the turn box
    expect(ui_page.locator(".turn .note")).to_contain_text("portal 1 needs the site: filter")
    # a truncated util observation says where the full output was saved (util.fullOutput),
    # so a reader of the run knows the elided middle still exists on disk
    obs = ui_page.locator(".obs-collapse").first
    obs.locator("summary").click()
    expect(obs).to_contain_text(".util_outputs/20260715-150000/t1-websearch.out")
    expect(obs).to_contain_text("41234 chars")
    # the injected message renders its reference line as a chip, body clean
    injection = ui_page.locator(".ev.injection")
    expect(injection.locator(".reply-ref")).to_contain_text("turn 1 (util websearch)")
    expect(injection).to_contain_text("user: look deeper")

    # ↩ on turn 1 primes the composer chip (label + the say as snippet)…
    ui_page.locator(".turn .refer-btn").first.click()
    ref = ui_page.locator(".composer-ref")
    expect(ref).to_be_visible()
    expect(ref).to_contain_text("turn 1 (util websearch): Catalog fits — scanning portals.")
    # …and the continued-run message leads with the quoted reference line (F233: a terminal
    # run's input always continues THIS run — there is no queue mode to select).
    ui_page.locator('textarea[placeholder^="message…"]').fill("dig into that result")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_contain_text("continue the conversation")
    expect(ref).to_be_hidden()                      # sent — the chip clears
    sent = [json.loads(m.read_text(encoding="utf-8"))
            for m in (ui.routine_dir("uir") / "inbox").glob("msg-*.json")]
    assert any(d["text"] == "> re turn 1 (util websearch): Catalog fits — scanning "
                            "portals.\n\ndig into that result" for d in sent)


# ---- 2. Conversation composer ------------------------------------------------------------


def test_conversation_composer(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    # D57/F244: the pre-start settings are exposed as titled sections (the same section
    # vocabulary the routine page uses), not buried in one opaque "capabilities & budgets"
    # disclosure. Each must be a visible <h2> on the composer before the conversation starts.
    for section in ("Model", "Budgets", "Deliberation", "Permissions & capabilities"):
        expect(ui_page.get_by_role("heading", name=section, exact=True)).to_be_visible()
    ui_page.locator(".conv-new textarea").fill(
        "Plan my week: gather the calendar, draft a schedule.")
    ui_page.get_by_role("button", name="start conversation").click()

    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    conv_dir = ui.conversations / slug
    assert (conv_dir / "instruction.md").read_text(encoding="utf-8").startswith("Plan my week")
    assert ui.runner.fired and ui.runner.fired[-1] == (slug, "conversation")
    # the first message is seeded into the chat immediately
    expect(ui_page.locator(".msg.user").first).to_contain_text("Plan my week")

    # a follow-up lands in the inbox and wakes the conversation through the runner
    ui_page.locator(".conv-composer textarea").fill("also include the gym")
    ui_page.locator(".conv-composer").get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_be_visible()
    # The composer clears the textarea and the server persists the inbox file only AFTER the
    # apiUpload round-trip resolves; a toast alone (any lingering toast satisfies to_be_visible
    # under xdist load) is NOT proof the send landed. Poll for the file itself — the standing
    # anti-flake rule for disk asserts — so this does not read the inbox before the write lands.
    _wait_until(lambda: len(list((conv_dir / "inbox").glob("msg-*.json"))) == 1)
    messages = list((conv_dir / "inbox").glob("msg-*.json"))
    assert len(messages) == 1
    assert "gym" in messages[0].read_text(encoding="utf-8")

    # F295: the sent message is echoed into the chat AT ONCE as a pending bubble — no
    # transcript event carries it until the woken leg boots (the stub never boots one) —
    # and the echo must SURVIVE the ~700ms post-send remount instead of vanishing with it.
    pending = ui_page.locator(".msg.user.pending")
    expect(pending).to_contain_text("also include the gym")
    expect(pending.locator(".pending-hint")).to_contain_text("sent")
    ui_page.wait_for_timeout(1200)   # outlive the post-send remount
    expect(pending).to_contain_text("also include the gym")


def test_conversation_slash_commands(ui, ui_page):
    """The chat composer's command surface: the reference panel lists actions + utils,
    typing / opens autocomplete, accepting fills the input, and a sent command is flagged
    for engine execution instead of going to the model as prose."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Command playground.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]

    # the reference panel next to the input
    ui_page.get_by_role("button", name="/ commands").click()
    help_panel = ui_page.locator(".cmd-help")
    expect(help_panel).to_be_visible()
    expect(help_panel).to_contain_text("/read_file <path>")
    expect(help_panel).to_contain_text("dir-tree")          # a seed util made the list

    # autocomplete on "/": filter, click to accept, util names complete after "/util "
    composer_input = ui_page.locator(".conv-composer textarea")
    composer_input.fill("/re")
    suggest = ui_page.locator(".cmd-suggest")
    expect(suggest).to_be_visible()
    # the dropdown floats over the chat — an undefined CSS token here once rendered it
    # transparent (unreadable), so pin an OPAQUE background
    bg = suggest.evaluate("el => getComputedStyle(el).backgroundColor")
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), f"dropdown background is {bg}"
    suggest.locator(".cs-item", has_text="/read_file").click()
    expect(composer_input).to_have_value("/read_file ")
    composer_input.fill("/util dir")
    expect(suggest.locator(".cs-item", has_text="/util dir-tree")).to_be_visible()

    # a sent command is marked for the engine to EXECUTE, and the toast confirms the turn
    # stays with the user (no reply handed to the model — a plain message would say "waking")
    composer_input.fill("/read_file instruction.md")
    ui_page.locator(".conv-composer").get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_contain_text("you keep the turn")
    flagged = [json.loads(m.read_text(encoding="utf-8"))
               for m in (ui.conversations / slug / "inbox").glob("msg-*.json")]
    command = next(d for d in flagged if d.get("command"))
    assert command["text"] == "/read_file instruction.md"
    # a bare word (not a known /kind) is NOT flagged — it would hand the turn to the model
    assert all("read_file" in d["text"] or not d.get("command") for d in flagged)


def test_conversation_deliberation_slider(ui, ui_page):
    """A conversation's deliberation is edited from the header panel: defaults to
    'deliberate' (chat is judgment-heavy), one arrow key saves the new level to the
    conversation's tuning.yaml — routine.yaml (config) stays untouched."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Deliberation knob playground.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    conv_dir = ui.conversations / slug
    tuning = yaml.safe_load((conv_dir / "tuning.yaml").read_text(encoding="utf-8"))
    assert tuning["deliberation"] == "deliberate"     # the conversation default

    ui_page.locator("summary", has_text="capabilities & budgets").click()
    slider = ui_page.locator('.delib input[type="range"]')
    slider.focus()
    slider.press("ArrowLeft")                         # deliberate → standard
    expect(_toast(ui_page)).to_contain_text("deliberation: standard")
    tuning = yaml.safe_load((conv_dir / "tuning.yaml").read_text(encoding="utf-8"))
    assert tuning["deliberation"] == "standard"
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8"))
    assert "deliberation" not in raw                  # config never carries tuning


def test_conversation_refer_to_message(ui, ui_page):
    """Messenger-style 'refer to' in chat: ↩ on a message primes the composer chip, ✕ drops
    it, and a sent message leads with the quoted reference line."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Sort my reading list.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]

    # the seeded instruction bubble carries the hover ↩ — clicking primes the chip
    ui_page.locator(".msg.user .refer-btn").first.click()
    ref = ui_page.locator(".composer-ref")
    expect(ref).to_be_visible()
    expect(ref).to_contain_text("my earlier message: Sort my reading list.")
    ref.get_by_role("button").click()               # ✕ drops the reference
    expect(ref).to_be_hidden()

    # primed again, the sent text leads with the quoted reference line
    ui_page.locator(".msg.user .refer-btn").first.click()
    ui_page.locator(".conv-composer textarea").fill("start with the papers")
    ui_page.locator(".conv-composer").get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_be_visible()
    messages = list((ui.conversations / slug / "inbox").glob("msg-*.json"))
    assert len(messages) == 1
    # exact match matters: multipart encodes newlines CRLF and the API must canonicalize
    # to \n, or every stored chat message would carry \r into the engine's context
    text = json.loads(messages[0].read_text(encoding="utf-8"))["text"]
    assert text == ("> re my earlier message: Sort my reading list.\n\n"
                    "start with the papers"), repr(text)


# ---- 3. Routine page saves ---------------------------------------------------------------


def test_routine_page_saves(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/routine/uir")
    desc = ui_page.locator('input[placeholder="one-line description"]')
    expect(desc).to_have_value("A test routine.")
    desc.fill("A sharper one-line description.")
    ui_page.get_by_role("button", name="save description").click()
    expect(_toast(ui_page)).to_contain_text("description saved")

    budgets_panel = ui_page.locator(
        ".panel", has=ui_page.get_by_role("button", name="save budgets"))
    budgets_panel.locator('input[type="number"]').first.fill("42")   # max_turns leads the list
    ui_page.get_by_role("button", name="save budgets").click()
    expect(_toast(ui_page)).to_contain_text("budgets saved")

    # tags: the shared editor saves each change immediately — no save button
    tag_input = ui_page.locator(".tags input")
    tag_input.fill("nightly")
    tag_input.press("Enter")
    expect(_toast(ui_page)).to_contain_text("tags saved")
    expect(ui_page.locator(".tags .tag", has_text="nightly")).to_be_visible()

    # deliberation slider (Models panel): one arrow key saves the level immediately
    delib_slider = ui_page.locator('.delib input[type="range"]')
    delib_slider.focus()
    delib_slider.press("ArrowRight")   # standard → deliberate
    expect(_toast(ui_page)).to_contain_text("deliberation: deliberate")

    # schedule: saves in place — the page must NOT reload (marker survives)
    ui_page.evaluate("window.__no_reload = true")
    ui_page.locator(".panel", has=ui_page.get_by_role("button", name="save schedule")) \
        .get_by_role("checkbox").first.uncheck()   # enabled off
    ui_page.get_by_role("button", name="save schedule").click()
    expect(_toast(ui_page)).to_contain_text("schedule saved")
    ui_page.wait_for_timeout(600)   # the old reload fired at 400ms — outlive it
    assert ui_page.evaluate("window.__no_reload") is True

    raw = yaml.safe_load(
        (ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["description"] == "A sharper one-line description."
    assert raw["budgets"]["max_turns"] == 42
    assert raw["tags"] == ["nightly"]
    assert raw["enabled"] is False
    assert "deliberation" not in raw   # tuning, not config — it lands in tuning.yaml
    tuning = yaml.safe_load(
        (ui.routine_dir("uir") / "tuning.yaml").read_text(encoding="utf-8"))
    assert tuning["deliberation"] == "deliberate"
    # removing the tag also saves immediately — wait on the DISK state, not a fixed sleep
    # (the removal has no distinct toast to sync on; a 200ms nap flaked under xdist load)
    ui_page.locator(".tags .tag", has_text="nightly").locator(".x").click()
    expect(ui_page.locator(".tags .tag", has_text="nightly")).to_have_count(0)
    _wait_until(lambda: yaml.safe_load(
        (ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))["tags"] == [])

    # permissions: the panel re-renders in place from the server's post-cascade state
    perm_panel = ui_page.locator(
        ".panel", has=ui_page.get_by_role("button", name="save permissions"))
    perm_panel.locator(".toggle-row input").first.check()
    ui_page.get_by_role("button", name="save permissions").click()
    expect(_toast(ui_page)).to_contain_text("permissions saved")
    ui_page.wait_for_timeout(600)
    assert ui_page.evaluate("window.__no_reload") is True
    raw = yaml.safe_load(
        (ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["permissions"]   # the toggled doc landed in config without a reload


def test_routine_page_permission_help_and_doc_expand(ui, ui_page):
    """F178: the permissions panel explains itself — capability rows carry concrete
    example help, and conduct-permission / practice-module rows expand to the FULL
    library doc (the same prose the run's prompt receives)."""
    ui_page.goto(f"{ui.url}/#/routine/uir")
    perm_panel = ui_page.locator(
        ".panel", has=ui_page.get_by_role("button", name="save permissions"))
    # capability rows explain themselves with examples (bare kind/util names told nothing)
    expect(perm_panel.get_by_text("become a proper util", exact=False)).to_be_visible()
    expect(perm_panel.get_by_text("two-hour bulk conversion", exact=False)).to_be_visible()

    # a conduct-permission row expands to the full library doc without flipping its checkbox
    row = perm_panel.locator(
        ".perm-doc", has=ui_page.locator(".t-title", has_text="memory")).first
    box = row.locator('input[type="checkbox"]')
    checked_before = box.is_checked()
    row.get_by_role("button", name="full description").click()
    expect(row.locator(".doc-expand-body")).to_be_visible()
    expect(row.locator(".doc-expand-body")).to_contain_text("notebook")
    assert box.is_checked() == checked_before

    # general-rule rows expand the same way
    rule_row = ui_page.locator(".rule-doc").first
    rule_row.get_by_role("button", name="full description").click()
    expect(rule_row.locator(".doc-expand-body")).to_be_visible()
    expect(rule_row.locator(".doc-expand-body")).not_to_be_empty()


# ---- 3b. Spend surfaces (dashboard card line + Stats monthly table) -----------------------


def test_spend_surfaces(ui, ui_page):
    entries = [
        {"ts": "2026-06-10T08:00:00+00:00", "routine": "uir", "depth": 0,
         "tokens": 900_000, "cost": 0.9},
        {"ts": "2026-07-10T08:00:00+00:00", "routine": "uir", "depth": 0,
         "tokens": 2_000_000, "cost": 2.0},
    ]
    ctrl = ui.routines / ".control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "workflow-usage.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    ui.seed_run("uir", "20260714-070000", "finished", summary="ran",
                usage={"in": 10, "out": 5, "cost": 0.01})

    ui_page.goto(f"{ui.url}/#/routines")   # dashboard: the card carries the compact month line
    ui_page.get_by_role("button", name="▦ card view").click()   # list is the default (D72)
    card = ui_page.locator(".card", has_text="Test uir")
    expect(card).to_contain_text("Jul: 2.00M tok")
    expect(card).to_contain_text("Jun: 900.0k tok")
    expect(card.locator(".chip.partial", has_text="growing")).to_be_visible()

    ui_page.goto(f"{ui.url}/#/stats")   # stats: the monthly table with the trend chip
    section = ui_page.locator(".stat-section", has_text="Monthly spend by routine")
    expect(section).to_be_visible()
    row = section.locator("tbody tr", has_text="uir")
    expect(row).to_contain_text("2.00M · $2.00")
    expect(row.locator(".chip.partial", has_text="growing")).to_be_visible()


# ---- 3c. Library deletes (rules/utils/workflows — permissions + clarify protected) -------


def test_library_tag_autosuggest_filters(ui, ui_page):
    """User order 2026-08-13: the every-tag chip wall is retired — ONE autosuggest input
    filters the library. Committing a suggested tag narrows the sections, shows it as a
    removable chip, and keeps the filter in the URL; removing the chip restores the list."""
    ui_page.goto(f"{ui.url}/#/library")
    inp = ui_page.locator("[data-tag-filter]")
    expect(inp).to_be_visible(timeout=10_000)
    total = ui_page.locator("table.list tr").count()
    tag = ui_page.locator("#lib-tag-suggest option").first.get_attribute("value")
    inp.fill(tag)
    inp.press("Enter")
    expect(ui_page.locator(".filterbar .tag.on", has_text=tag)).to_be_visible(
        timeout=10_000)
    assert f"tags={tag}" in ui_page.url
    assert ui_page.locator("table.list tr").count() < total, \
        "committing a tag should narrow the sections"
    # free text that is no tag never filters
    inp.fill("no-such-tag-xyz")
    inp.press("Enter")
    assert "no-such-tag-xyz" not in ui_page.url
    # removing the chip restores the full list
    ui_page.locator(".filterbar .tag.on", has_text=tag).click()
    deadline = total
    expect(inp).to_have_attribute("placeholder", "filter by tag…", timeout=10_000)
    assert ui_page.locator("table.list tr").count() == deadline


def test_library_delete_flows(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/library")
    editor_panel = ui_page.locator(
        ".panel", has=ui_page.get_by_role("button", name="save + commit"))

    # a rule deletes through the themed dialog; the reload lands on the bare list
    ui_page.get_by_role("link", name="ask-policy", exact=True).click()
    editor_panel.get_by_role("button", name="delete").click()
    _confirm_modal(ui_page, "delete")
    expect(ui_page.get_by_role("link", name="ask-policy", exact=True)).to_have_count(0)
    assert not (ui.tmp / "library" / "rules" / "ask-policy.md").exists()
    assert "#/library" in ui_page.url and "rule/" not in ui_page.url

    # a util deletes the same way (whole dir, git-recoverable)
    ui_page.get_by_role("link", name="dir-tree", exact=True).click()
    editor_panel.get_by_role("button", name="delete").click()
    _confirm_modal(ui_page, "delete")
    expect(ui_page.get_by_role("link", name="dir-tree", exact=True)).to_have_count(0)
    assert not (ui.tmp / "library" / "utils" / "dir-tree").exists()

    # a permission opens WITHOUT any delete affordance
    ui_page.get_by_role("link", name="memory", exact=True).click()
    expect(editor_panel.get_by_role("button", name="save + commit")).to_be_visible()
    expect(editor_panel.get_by_role("button", name="delete")).to_have_count(0)

    # clarify-instruction: editable, NOT deletable; its sibling workflows are
    ui_page.goto(f"{ui.url}/#/library/workflow/clarify-instruction")
    expect(editor_panel.get_by_role("button", name="save + commit")).to_be_visible()
    expect(editor_panel.get_by_role("button", name="delete")).to_have_count(0)
    ui_page.goto(f"{ui.url}/#/library/workflow/general-task")
    expect(editor_panel.get_by_role("button", name="delete")).to_be_visible()


# ---- 4. Settings endpoints CRUD ----------------------------------------------------------


def _server_yaml(ui) -> dict:
    return yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))


def _confirm_modal(page, label):
    """Answer the themed confirm dialog (components/dialog.js) — native dialogs are gone;
    one appearing anywhere would block and fail the test, which is the point."""
    page.locator(".modal-overlay").get_by_role("button", name=label, exact=True).click()


def test_settings_endpoints_crud(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/settings?section=endpoints")

    # CREATE an endpoint
    add = ui_page.locator("details.panel", has_text="+ add endpoint")
    add.locator("summary").click()
    add.locator('input[placeholder="name (e.g. openrouter)"]').fill("vllm")
    add.locator('input[placeholder="https://host/v1"]').fill("http://10.0.0.5:8000/v1")
    add.get_by_role("button", name="add endpoint", exact=True).click()
    card = ui_page.locator(".panel", has=ui_page.locator("strong", has_text="vllm")).first
    expect(card).to_contain_text("http://10.0.0.5:8000/v1")
    assert _server_yaml(ui)["endpoints"]["vllm"]["base_url"] == "http://10.0.0.5:8000/v1"
    # the credential-source indicator: no key anywhere (hermetic secrets) → keyless label
    expect(card).to_contain_text("credential in use:")
    expect(card).to_contain_text("keyless local backends")

    # UPDATE it (edit fields → save changes)
    card.locator("summary", has_text="edit fields").click()
    card.locator('input[placeholder="https://host/v1"]').fill("http://10.0.0.6:8000/v1")
    card.get_by_role("button", name="save changes").click()
    # Wait for the list to RE-RENDER with the persisted value before reading config.yaml.
    # The save handler runs `await api(PUT); toast(...); await load()`, so the card header only
    # shows the new base_url once load() has re-fetched post-write. Asserting on a lingering
    # toast instead (the CREATE toast may still be on screen) races the config write and flakes
    # under xdist — the DOM re-render is the reliable "the save round-tripped" signal.
    expect(card).to_contain_text("http://10.0.0.6:8000/v1")
    assert _server_yaml(ui)["endpoints"]["vllm"]["base_url"] == "http://10.0.0.6:8000/v1"

    # CREATE a catalog model bound to it
    addm = ui_page.locator("details.panel", has_text="+ add model")
    addm.locator("summary").click()
    addm.locator('input[placeholder="name (e.g. gpt-4o)"]').fill("llama")
    addm.locator("select").first.select_option("vllm")
    addm.locator('input[placeholder="model id (e.g. openai/gpt-4o)"]').fill("meta/llama-3")
    addm.get_by_role("button", name="add model", exact=True).click()
    expect(ui_page.locator("strong", has_text="llama").first).to_be_visible()
    models = _server_yaml(ui)["models"]
    assert models["llama"] == {"endpoint": "vllm", "model": "meta/llama-3"} \
        or models["llama"]["endpoint"] == "vllm"

    # the system-model blurb states the role-fallback behaviour (not "setup-time work" only)
    expect(ui_page.locator(".panel", has_text="System model"))\
        .to_contain_text("any role a routine leaves unset falls back to this system model")

    # max_tokens audit flag: unset → the ⚠ chip; a self-referencing fallback is rejected
    # server-side; setting a real value clears the flag
    model_card = ui_page.locator(".panel",
                                 has=ui_page.locator("strong", has_text="llama")).last
    expect(model_card).to_contain_text("⚠ max_tokens")
    model_card.locator("summary", has_text="edit fields").click()
    model_card.locator("label.field", has_text="max_tokens (output)").locator("input").fill("8192")
    model_card.locator("label.field", has_text="fallbacks").locator("input").fill("llama")
    model_card.get_by_role("button", name="save changes").click()
    expect(_toast(ui_page)).to_contain_text("fallback")
    model_card.locator("label.field", has_text="fallbacks").locator("input").fill("")
    model_card.get_by_role("button", name="save changes").click()
    model_card = ui_page.locator(".panel",
                                 has=ui_page.locator("strong", has_text="llama")).last
    expect(model_card).not_to_contain_text("⚠ max_tokens")   # auto-waits for the reload
    assert _server_yaml(ui)["models"]["llama"]["max_tokens"] == 8192

    # DELETE the model, then the endpoint (each behind the themed confirm dialog).
    # .last = the INNERMOST matching panel (the card), not the section wrapper around it.
    model_card = ui_page.locator(".panel",
                                 has=ui_page.locator("strong", has_text="llama")).last
    model_card.get_by_role("button", name="delete").click()
    _confirm_modal(ui_page, "cancel")            # cancelling keeps the model
    expect(ui_page.locator("strong", has_text="llama").first).to_be_visible()
    assert "llama" in _server_yaml(ui)["models"]
    model_card.get_by_role("button", name="delete").click()
    _confirm_modal(ui_page, "delete")
    expect(ui_page.locator("strong", has_text="llama")).to_have_count(0)
    endpoint_card = ui_page.locator(".panel",
                                    has=ui_page.locator("strong", has_text="vllm")).first
    endpoint_card.get_by_role("button", name="delete").click()
    _confirm_modal(ui_page, "delete")
    expect(ui_page.locator("strong", has_text="vllm")).to_have_count(0)
    cfg = _server_yaml(ui)
    assert "vllm" not in (cfg.get("endpoints") or {})
    assert "llama" not in (cfg.get("models") or {})   # deleting the last model may null the key


def test_settings_grouped_layout(ui, ui_page):
    """The Settings page groups its sections into four labelled categories with a per-group
    blurb and a per-section description (F248 cognitive-model overhaul), while keeping every
    stable sec-<id> anchor and the ?section deep-link jump the TOC and other tests rely on."""
    ui_page.set_viewport_size({"width": 1600, "height": 1000})
    ui_page.goto(f"{ui.url}/#/settings")
    ui_page.wait_for_selector("#sec-endpoints", timeout=10_000)

    # the four cognitive-model group eyebrows are present, in order
    groups = ui_page.locator(".set-group .kicker")
    expect(groups).to_have_count(4)
    for i, label in enumerate(["Intelligence", "Connections", "Code", "This instance"]):
        expect(groups.nth(i)).to_have_text(label)

    # every section carries a plain reader-side description, and a group carries a why-blurb
    # (8 since 0.192.0: the library repo has no settings surface — library-sync owns it)
    expect(ui_page.locator(".set-desc")).to_have_count(8)
    expect(ui_page.locator("p.set-desc").first).not_to_be_empty()
    expect(ui_page.locator(".set-groupblurb").first).to_contain_text("reasoning")

    # the grouped nav labels mirror the groups (not one flat "section" label)
    expect(ui_page.locator(".settings-nav .lbl", has_text="Intelligence")).to_be_visible()
    expect(ui_page.locator(".settings-nav .lbl", has_text="This instance")).to_be_visible()

    # REGRESSION: the stable id + deep-link jump still work after the restructure
    ui_page.goto(f"{ui.url}/#/settings?section=notifications")
    expect(ui_page.locator("#sec-notifications")).to_be_in_viewport()


# ---- 6. Pre-start capabilities & budgets on the composer ----------------------------------


def test_new_conversation_composer_offers_caps_and_budgets(ui, ui_page):
    """The composer exposes the SAME permission + budget controls as the conversation
    header — a permission granted here (e.g. shell) and a budget set here govern reply #1,
    which fires on create and would miss any post-hoc toggle. D57: they are titled sections
    (Permissions & capabilities, Budgets), no longer buried in one ⚙ disclosure."""
    import re

    ui_page.goto(f"{ui.url}/#/conversations")
    expect(ui_page.get_by_role("heading", name="Permissions & capabilities",
                               exact=True)).to_be_visible()
    shell_row = ui_page.locator(".toggle-row").filter(
        has=ui_page.get_by_text("shell", exact=True))
    shell_row.locator('input[type="checkbox"]').check()
    ui_page.locator('input[title="max tokens per reply (-1 = unlimited)"]').fill("55000")
    ui_page.locator(".conv-new textarea").fill("Need a shell for this.")
    ui_page.get_by_role("button", name="start conversation").click()
    expect(ui_page).to_have_url(re.compile(r"#/conversations/"))
    convs = [p for p in ui.conversations.iterdir() if (p / "routine.yaml").exists()]
    assert len(convs) == 1
    raw = yaml.safe_load((convs[0] / "routine.yaml").read_text(encoding="utf-8"))
    assert "shell" in raw["permissions"]
    assert "shell" in raw["capabilities"]["utils"]
    assert raw["budgets"]["max_total_tokens"] == 55000
    tuning = yaml.safe_load((convs[0] / "tuning.yaml").read_text(encoding="utf-8"))
    assert tuning["deliberation"] == "deliberate"   # the untouched default rides along


# ---- 7. Item reference links (F63/D14 → the card they name) --------------------------------


def test_item_refs_link_and_flash(ui, ui_page):
    """D[n]/F[n] mentions in the audit report are hyperlinks to the item card they reference;
    following one lands on (and flashes) that card, and the Decisions page's meta items
    carry the same links."""
    import re

    rdir = ui.routines / "self-audit"
    (rdir / "audit").mkdir(parents=True)
    report = {
        "generated": "2026-07-16T20:00:00+00:00",
        "summary": "F1 is carried this run; D1 awaits you.",
        "findings": [{"id": "F1", "severity": "info", "title": "Watch item",
                      "status": "open", "detail": "Blocked on D1."}],
        "decisions": [{"id": "D1", "status": "open", "title": "Pick a path",
                       "detail": "See F1 for the evidence.",
                       "options": ["do it", "leave as-is"]}],
    }
    (rdir / "audit" / "report.json").write_text(json.dumps(report), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/messages")
    link = ui_page.locator(".panel.prose a.ref-link", has_text="D1")
    expect(link).to_have_attribute("href", "#/messages?focus=D1")
    expect(ui_page.locator("#ref-F1")).to_be_visible()      # findings AND decisions get cards
    expect(ui_page.locator("#ref-D1")).to_contain_text("Pick a path")
    link.click()                                            # follow the ref → land + flash
    expect(ui_page.locator("#ref-D1")).to_have_class(re.compile(r"ref-flash"))

    ui_page.goto(f"{ui.url}/#/questions")                   # the same ids link from the inbox
    card = ui_page.locator(".question-item", has_text="Pick a path")
    flink = card.locator("a.ref-link", has_text="F1")
    expect(flink).to_have_attribute("href", "#/messages?focus=F1")


def test_decision_detail_renders_markdown(ui, ui_page):
    """A self-audit report DECISION carries rich markdown in its `detail` (the report's own
    prose — `code`, bullet lists, tables). On the Decisions page (#/questions) a meta decision
    must render that markdown as real DOM, not literal text. Regression: open questions used to
    render as raw textContent and answered ones inline-only, so decision block markdown (lists,
    tables, code fences) never rendered — the reviewer flagged it (2026-07-18)."""
    rdir = ui.routines / "self-audit"
    (rdir / "audit").mkdir(parents=True)
    report = {
        "generated": "2026-07-16T20:00:00+00:00",
        "summary": "one open decision.",
        "findings": [],
        "decisions": [{
            "id": "D9", "status": "open", "title": "Adopt the `snapshot` guard",
            "detail": ("Two independent options:\n\n"
                       "- keep `write_util_stats_snapshot` as-is\n"
                       "- add a `log.warning` breadcrumb\n"),
            "options": ["do it", "leave as-is"]}],
    }
    (rdir / "audit" / "report.json").write_text(json.dumps(report), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item", has_text="Adopt the")
    # the bullet list in `detail` renders as a real <li>, not literal "- add …"
    expect(card.locator("li", has_text="breadcrumb")).to_be_visible()
    # inline `code` spans render as <code>, not literal backticks
    expect(card.locator("code", has_text="log.warning")).to_be_visible()
    # and the raw list marker is gone from the visible text
    assert "- keep" not in card.inner_text()


def test_item_detail_renders_markdown(ui, ui_page):
    """The Messages page (#/messages) renders an item's own prose — finding/decision `detail` and
    the report summary — as real markdown DOM, not literal textContent. Regression (F105,
    2026-07-18): the audit view Items replaced never imported md.js, so block markdown
    (lists, `code`, tables) showed as raw text — the same gap F104 fixed on the Decisions
    page. Ref-links (F/D/R mentions) must still work through the md() output."""

    rdir = ui.routines / "self-audit"
    (rdir / "audit").mkdir(parents=True)
    report = {
        "generated": "2026-07-16T20:00:00+00:00",
        "summary": "Carrying F2; D2 is settled.",
        "findings": [{
            "id": "F2", "severity": "improvement", "title": "Render `detail` as markdown",
            "status": "open",
            "detail": ("The Audit view rendered prose flat. Now:\n\n"
                       "- lists become real items\n"
                       "- inline `code` renders\n")}],
        "decisions": [{
            "id": "D2", "status": "open", "title": "Ship the md() fix",
            "detail": ("Two options:\n\n"
                       "- apply the `md()` render (see F2)\n"
                       "- leave as-is\n"),
            "options": ["apply", "leave as-is"]}],
    }
    (rdir / "audit" / "report.json").write_text(json.dumps(report), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/messages")
    fcard = ui_page.locator("#ref-F2")
    # the finding's bullet list renders as real <li>, not literal "- lists become …"
    expect(fcard.locator("li", has_text="lists become real")).to_be_visible()
    expect(fcard.locator("code", has_text="code")).to_be_visible()
    assert "- lists become" not in fcard.inner_text()
    # decision detail renders markdown too, and a D-ref inside md() prose still linkifies
    dcard = ui_page.locator("#ref-D2")
    expect(dcard.locator("li", has_text="apply the")).to_be_visible()
    expect(dcard.locator("code", has_text="md()")).to_be_visible()
    # (scoped to .md: the card also carries a compact "refers to" index of the same ids)
    expect(dcard.locator(".md a.ref-link", has_text="F2")).to_have_attribute(
        "href", "#/messages?focus=F2")


# ---- 8. md.js block rendering: GFM pipe tables + blockquotes -------------------------------


def test_md_tables_and_blockquotes_render(ui, ui_page):
    """The one sanctioned innerHTML pathway renders GFM pipe tables (reusing table.list)
    and > blockquotes on block surfaces — here the finish summary, the most-read one.
    Inline transforms still run inside cells; a table without a valid |---| separator
    stays literal text (the malformed-input contract)."""
    summary = (
        "## Digest\n\n"
        "| portal | hits | best |\n"
        "| --- | ---: | --- |\n"
        "| freelance.de | 12 | **9** |\n"
        "| gulp | 3 | 7 |\n\n"
        "> re yesterday: the floor stays at 80.\n"
        "> Flag anything above 110 anyway.\n\n"
        "| not | a table |\n"
        "plain prose right after it\n")
    run_dir = ui.seed_run("uir", "20260716-090000", "finished", summary="done")
    finish = {"ts": "2026-07-16T09:05:00+00:00", "type": "finish", "turns": 3,
              "usage_total": {"in": 10, "out": 5},
              "payload": {"status": "ok", "summary": summary, "authored": True}}
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(finish) + "\n")

    ui_page.goto(f"{ui.url}/#/run/uir:20260716-090000")
    banner = ui_page.locator(".finish-banner")
    table = banner.locator("table.list")
    expect(table).to_have_count(1)                          # the malformed one did NOT parse
    expect(table.locator("th").nth(0)).to_have_text("portal")
    expect(table.locator("tbody tr")).to_have_count(2)
    expect(table.locator("tbody tr").nth(0)).to_contain_text("freelance.de")
    expect(table.locator("strong")).to_have_text("9")       # inline md runs inside cells
    quote = banner.locator("blockquote")
    expect(quote).to_contain_text("re yesterday: the floor stays at 80.")
    expect(quote).to_contain_text("Flag anything above 110 anyway.")
    expect(banner).to_contain_text("| not | a table |")     # literal text, pipes intact


def test_md_ordered_lists_enumerate_sequentially(ui, ui_page):
    """Ordered markdown lists render with the AUTHORED numbers (F266/R103): the model's
    `1. 2. 3.` must show as 1, 2, 3 — including when items are separated by blank lines
    (each item then becomes its own <ol>, which would otherwise restart at 1) and when the
    list starts at a number other than 1. The parser stamps <ol start> + <li value> so the
    rendered numbering matches the source, the way GitHub renders it."""
    summary = (
        "## Steps\n\n"
        "1. first step\n"
        "2. second step\n"
        "3. third step\n\n"
        "Then, separated by blank lines:\n\n"
        "1. alpha\n\n"
        "2. beta\n\n"
        "3. gamma\n")
    run_dir = ui.seed_run("uiol", "20260716-091000", "finished", summary="done")
    finish = {"ts": "2026-07-16T09:12:00+00:00", "type": "finish", "turns": 3,
              "usage_total": {"in": 10, "out": 5},
              "payload": {"status": "ok", "summary": summary, "authored": True}}
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(finish) + "\n")

    ui_page.goto(f"{ui.url}/#/run/uiol:20260716-091000")
    banner = ui_page.locator(".finish-banner")
    # The contiguous list is one <ol> whose items carry values 1,2,3.
    contiguous = banner.locator("ol").first
    expect(contiguous.locator("li")).to_have_count(3)
    for idx, val in enumerate(("1", "2", "3")):
        expect(contiguous.locator("li").nth(idx)).to_have_attribute("value", val)
    # The blank-line-separated items each become their own <ol start=N> — the second item is
    # NOT re-numbered "1": its single <li> carries value 2, the third value 3.
    li_beta = banner.locator('li[value="2"]', has_text="beta")
    expect(li_beta).to_have_count(1)
    li_gamma = banner.locator('li[value="3"]', has_text="gamma")
    expect(li_gamma).to_have_count(1)


# ---- 9. Dashboard run-history heartbeat strip ----------------------------------------------


def test_dashboard_heartbeat_strip(ui, ui_page):
    """The heartbeat strip answers 'is this routine RELIABLE, not just green today': one
    bar per recent run on the card AND the list view, outcome-bucketed (partial comes from
    status.json `outcome` — state alone reads finished), newest at the right edge, click
    opens that run."""
    ui.seed_run("uir", "20260710-070000", "finished", summary="ok run",
                usage={"in": 100, "out": 20, "cost": 0.02})
    partial_dir = ui.seed_run("uir", "20260711-070000", "finished", summary="stopped early",
                              usage={"in": 50, "out": 10})
    st = json.loads((partial_dir / "status.json").read_text(encoding="utf-8"))
    st["outcome"] = "partial"
    (partial_dir / "status.json").write_text(json.dumps(st), encoding="utf-8")
    ui.seed_run("uir", "20260712-070000", "failed", summary="boom")
    ui.seed_run("uir", "20260713-070000", "aborted")

    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.get_by_role("button", name="▦ card view").click()   # list is the default (D72)
    card = ui_page.locator(".card", has_text="Test uir")
    strip = card.locator("svg.heartbeat")
    expect(strip).to_be_visible()
    for cls in ("hb-ok", "hb-partial", "hb-failed", "hb-aborted"):
        expect(strip.locator(f"rect.{cls}")).to_have_count(1)
    expect(strip.locator("rect.hb-empty")).to_have_count(11)   # 15 slots, 4 runs
    # newest run (the aborted one) sits at the right edge; its bar opens the run view
    strip.locator("a.hb-bar").last.click()
    expect(ui_page).to_have_url(f"{ui.url}/#/run/uir:20260713-070000")

    ui_page.goto(f"{ui.url}/#/routines")                                       # list view: same strip per row
    ui_page.get_by_role("button", name="☰ list view").click()
    row = ui_page.locator("table.list tbody tr", has_text="Test uir")
    expect(row.locator("svg.heartbeat")).to_be_visible()


def test_run_view_rewind_button_reopens_terminal_run(ui, ui_page):
    """D69: a terminal run's view offers a '⟲ rewind' control that re-opens the run at a chosen
    turn — the remedy for a run that died or derailed. It is hidden while the run is live,
    shown once terminal; clicking it prompts for a turn and POSTs /rewind with that turn."""
    import json as _json

    ui.seed_run("uir", "20260715-070000", "failed", summary="died on overflow")

    posted = {}

    def handle(route):
        posted["body"] = _json.loads(route.request.post_data or "{}")
        route.fulfill(status=200, content_type="application/json",
                      body=_json.dumps({"ok": True, "run_id": "uir:20260715-070000",
                                        "kept_through_turn": posted["body"].get("turn"),
                                        "kept_events": 3, "dropped_events": 4,
                                        "archive": "rewind-x.jsonl"}))
    ui_page.route("**/api/runs/uir:20260715-070000/rewind", handle)

    ui_page.goto(f"{ui.url}/#/run/uir:20260715-070000")
    rewind = ui_page.get_by_role("button", name="⟲ rewind")
    expect(rewind).to_be_visible()   # terminal run → the control is offered
    rewind.click()
    dlg = ui_page.locator(".modal-overlay .panel")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill("2")
    dlg.get_by_role("button", name="ok", exact=True).click()
    # the POST carried the entered turn — the button→prompt→endpoint wiring, end to end.
    # (The success toast is transient: setTimeout(remount, 800) tears the view down and clears
    # it, so asserting its visibility races the remount — the POST body is the real contract.)
    ui_page.wait_for_timeout(400)
    assert posted.get("body") == {"turn": 2}, posted


def test_dashboard_running_marker_in_both_views(ui, ui_page):
    """A routine with a run in flight is visually marked as running in BOTH the card view
    (.card.live — the mint left-edge) AND the list view (tr.live). The list view used to
    omit the marker entirely: its row only ever got the `attention` class (waiting on a
    question), never `live`, so a running routine looked idle in the table."""
    ui.seed_run("uir", "20260714-070000", "running")

    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.get_by_role("button", name="▦ card view").click()   # list is the default (D72)
    expect(ui_page.locator(".card.live", has_text="Test uir")).to_be_visible()

    ui_page.get_by_role("button", name="☰ list view").click()
    expect(ui_page.locator("table.list tbody tr.live", has_text="Test uir")).to_be_visible()


def test_dashboard_shows_group_membership(ui, ui_page):
    """R107/F269: a routine's group membership is visible on the Routines list — a group
    chip on the card AND in the list-view row — so groups are discoverable from the routines
    page. Clicking the chip opens the group's editor in place (D80: this page IS the
    group-management surface; the /groups subpage is retired)."""
    from rsched import groups

    rec = groups.create(ui.routines, name="Maintenance",
                        members=[{"slug": "uir", "split": False}], on_failure="stop")

    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.get_by_role("button", name="▦ card view").click()   # list is the default (D72)
    card = ui_page.locator(".card", has_text="Test uir")
    chip = card.locator("button.group-chip", has_text="Maintenance")
    expect(chip).to_be_visible()
    chip.click()
    expect(ui_page.locator(f'[data-group="{rec["id"]}"]')).to_be_visible(timeout=10_000)
    ui_page.locator("[data-group-editor-close]").click()

    # list view: a grouped routine lives ONLY under its group row (F281) — expand it,
    # then the member row carries the same chip
    ui_page.goto(f"{ui.url}/#/routines")
    ui_page.get_by_role("button", name="☰ list view").click()
    expect(ui_page.locator("table.list tbody tr", has_text="Test uir")).to_have_count(0)
    ui_page.locator("tr.group-row", has_text="Maintenance").get_by_text("⛓ Maintenance").click()
    row = ui_page.locator("tr.group-member", has_text="Test uir")
    expect(row.locator("button.group-chip", has_text="Maintenance")).to_be_visible()


def test_dashboard_list_default_group_rows_and_inline_pause(ui, ui_page):
    """D72+D73 (operator-selected 2026-08-05): the TABLE is the default routines view (the
    five compressed columns fit the normal shell column — the full-width breakout is
    retired); a group is a collapsible row whose expansion lists its members; and every row
    carries an inline ⏸ pause / ▷ resume that PATCHes `enabled` without a trip to the
    config page."""
    from rsched import groups

    groups.create(ui.routines, name="Nightly", members=[{"slug": "uir", "split": False}],
                  on_failure="stop")

    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator("table.list")).to_be_visible()   # no toggle click — the default

    # the group row: present, collapsed by default, expands to member rows, collapses back.
    # F281 (reviewer order 2026-08-06): a grouped routine appears ONLY under its group row —
    # collapsed means NO row for it anywhere, expanded means exactly one member row.
    # (D80 put the management buttons on the row, so the toggle clicks target the label.)
    grow = ui_page.locator("tr.group-row", has_text="Nightly")
    expect(grow).to_be_visible()
    expect(ui_page.locator("tr.group-member")).to_have_count(0)
    expect(ui_page.locator("table.list tbody tr", has_text="Test uir")).to_have_count(0)
    grow.get_by_text("⛓ Nightly").click()
    expect(ui_page.locator("tr.group-member", has_text="Test uir")).to_be_visible()
    expect(ui_page.locator("table.list tbody tr", has_text="Test uir")).to_have_count(1)
    ui_page.locator("tr.group-row", has_text="Nightly").get_by_text("⛓ Nightly").click()
    expect(ui_page.locator("tr.group-member")).to_have_count(0)

    # inline pause on the expanded MEMBER row (the only row a grouped routine has, F281):
    # the row controls are icon-only (⏸ pause / hollow ▷ resume — action text in the hover
    # title); pausing disables the routine on disk, dims the row, and re-renders…
    ui_page.locator("tr.group-row", has_text="Nightly").get_by_text("⛓ Nightly").click()
    ui_page.locator("table.list tbody tr", has_text="Test uir").last \
        .get_by_role("button", name="⏸").click()
    row = ui_page.locator("table.list tbody tr.disabled-row", has_text="Test uir")
    expect(row).to_be_visible(timeout=10_000)          # the dimmed row…
    expect(row.locator(".chip.disabled", has_text="off")).to_be_visible()   # …and the off tag
    cfg = yaml.safe_load((ui.routines / "uir" / "routine.yaml").read_text(encoding="utf-8"))
    assert cfg["enabled"] is False
    # …and resumes from the same control
    ui_page.locator("table.list tbody tr", has_text="Test uir").last \
        .get_by_role("button", name="▷").click()
    expect(ui_page.locator("table.list tbody tr", has_text="Test uir").last
           .get_by_role("button", name="⏸")).to_be_visible(timeout=10_000)


def test_dashboard_group_managed_schedule_shown(ui, ui_page):
    """R313: a member of a SCHEDULED group must not render its vestigial own cron — the
    daemon suppresses it, so the row shows the group's schedule (⛓ name — sentence) and
    the group header row carries the same sentence."""
    from rsched import groups

    # the member keeps a vestigial cron of its own — exactly the lying state R313 reported
    cfg_path = ui.routines / "uir" / "routine.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["cron"] = "0 11 * * *"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    groups.create(ui.routines, name="Sched", members=[{"slug": "uir", "split": False}],
                  on_failure="stop", cron="0 10 * * *", tz="Europe/Berlin")

    ui_page.goto(f"{ui.url}/#/routines")
    grow = ui_page.locator("tr.group-row", has_text="Sched")
    expect(grow).to_contain_text("Every day at 10:00")     # header names the group cron
    grow.get_by_text("⛓ Sched").click()                    # expand the member row
    # the schedule·next cell (routine · history · schedule·next · last run · controls)
    cell = ui_page.locator("tr.group-member", has_text="Test uir").locator("td").nth(2)
    expect(cell).to_contain_text("⛓ Sched — Every day at 10:00")
    expect(cell).not_to_contain_text("11:00")              # the vestigial cron is gone


def test_dashboard_table_sort_reverses_on_reclick(ui, ui_page):
    """F208: clicking a sortable column header sorts by it; re-clicking the ACTIVE column
    reverses the direction (was a no-op) — the header arrow shows ▴ asc / ▾ desc."""
    ui.seed_run("uir", "20260729-070000", "finished", summary="ok")
    ui_page.goto(f"{ui.url}/#/routines")            # the table IS the default view (D72)
    routine_th = ui_page.locator("table.list th", has_text="routine")
    routine_th.click()                                   # name column, natural ascending
    expect(routine_th).to_contain_text("▴")
    routine_th.click()                                   # re-click → reverse to descending
    expect(routine_th).to_contain_text("▾")
    routine_th.click()                                   # and back to ascending
    expect(routine_th).to_contain_text("▴")


def test_dashboard_live_refresh_preserves_search_focus(ui, ui_page):
    """F229: while ≥1 routine runs, live bus events refresh the dashboard ~every 600ms. The
    refresh must NOT tear down the filter bar (the search input + sort control) — doing so
    destroyed a user's focus and half-typed search text, the 'UI non-responsive with >1
    routine running' symptom. Typing survives a live refresh; the filter bar rebuilds only
    when the available tag set changes."""
    ui.seed_run("uir", "20260729-070000", "finished", summary="ok")
    ui_page.goto(f"{ui.url}/#/routines")
    search = ui_page.locator(".filterbar input[type=search]")
    expect(search).to_be_visible()
    search.click()
    search.type("uir")
    # a live bus event (as app.js dispatches from /api/events) with the tag set UNCHANGED
    ui_page.evaluate(
        "window.dispatchEvent(new CustomEvent('rsched-bus', {detail: {event: 'run_started'}}))")
    ui_page.wait_for_timeout(900)   # past the 600ms dashboard debounce
    # the SAME input node still holds focus and the typed text — not a fresh replaced node
    expect(search).to_be_focused()
    expect(search).to_have_value("uir")


def test_conversations_is_the_landing_page_and_first_in_nav(ui, ui_page):
    """Operator order: Conversations is the landing page and the first nav item. A bare-origin
    load (empty hash) opens the conversations view (its new-conversation composer); the
    Routines dashboard has moved to its own #/routines route; and the first nav link is
    Conversations. The brand link points home (→ conversations)."""
    import re

    ui.seed_run("uir", "20260730-070000", "finished", summary="ok")
    # first nav item is Conversations
    ui_page.goto(f"{ui.url}/#/routines")
    first = ui_page.locator("#nav a").first
    expect(first).to_have_text("Conversations")
    expect(first).to_have_attribute("data-nav", "conversations")
    # bare origin (empty hash) lands on conversations — the composer, not the routines dashboard
    ui_page.goto(ui.url)
    expect(ui_page.locator(".conv-new textarea")).to_be_visible()
    expect(ui_page.locator("[data-nav=conversations]")).to_have_class(re.compile(r"\bactive\b"))
    # the Routines dashboard is reachable at its own route
    ui_page.goto(f"{ui.url}/#/routines")
    expect(ui_page.locator(".filterbar input[type=search]")).to_be_visible()
    expect(ui_page.locator("[data-nav=dashboard]")).to_have_class(re.compile(r"\bactive\b"))


def test_global_stream_remints_ticket_on_reconnect(ui, ui_page):
    """F253: the global /api/events stream drives every view's live refresh (dashboard
    routine states, decision badges, run toasts). SSE tickets have a 60s TTL and are purged
    whenever the daemon restarts, but EventSource's native auto-reconnect reuses the SAME
    ?ticket= URL — so after any drop the reconnect 401s forever, the bus goes silent, and
    the console freezes with stale routine states (the daemon lamp stuck off). globalStream
    must own the reconnect: mint a FRESH ticket and reopen under backoff. Here the stream is
    routed so a reused ticket is rejected (as an expired/purged one would be) and the first
    connection is dropped; only re-minting recovers the daemon lamp."""
    from urllib.parse import parse_qs, urlparse

    seen: set[str] = set()

    def handle(route):
        ticket = parse_qs(urlparse(route.request.url).query).get("ticket", [""])[0]
        if ticket in seen:
            route.abort()          # a reused (expired/purged) ticket is rejected
        else:
            seen.add(ticket)
            route.abort()          # accept the ticket once, then drop it — forces a reconnect
    ui_page.route("**/api/events*", handle)

    ui.seed_run("uir", "20260729-070000", "finished", summary="ok")
    ui_page.goto(f"{ui.url}/#/routines")
    # Every ticket is dropped, so the lamp never stays on in EITHER version — the
    # discriminator is whether the client RE-MINTS. The unfixed client lets EventSource
    # retry the SAME dead ticket, so `seen` never grows past 1; the fix mints a fresh ticket
    # on each reconnect under backoff (first retry at ~1s), so distinct tickets accumulate.
    # Wait past the first backoff, then require ≥2 DISTINCT tickets.
    ui_page.wait_for_timeout(3000)
    assert len(seen) >= 2, (
        f"client did not re-mint a fresh ticket after the stream dropped (saw {len(seen)})")


def test_routine_page_rule_picker_binds_a_general_rule(ui, ui_page):
    """The post-creation rule picker: ticking a library rule and applying records the SLUG in
    routine.yaml and rebuilds main.md's derived Standing-practices tail. Nothing is copied —
    the prose stays in the library. This is the user's switch; a run never changes its set."""
    import yaml
    rdir = ui.routines / "uir"
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = ui_page.locator(".panel", has=ui_page.locator(".rulepicker"))
    expect(panel).to_be_visible()
    row = panel.locator("label.toggle-row", has_text="evidence-discipline")
    expect(row).to_be_visible()
    row.locator('input[type="checkbox"]').check()
    panel.get_by_role("button", name="apply").click()
    expect(_toast(ui_page)).to_contain_text("rules updated")
    held = yaml.safe_load((rdir / "routine.yaml").read_text(encoding="utf-8"))["rules"]
    assert "evidence-discipline" in held
    assert not (rdir / "rules").exists()               # one copy only, and it is the library's
    assert "`evidence-discipline`" in (rdir / "main.md").read_text(encoding="utf-8")

    ui_page.reload()                                   # the tick survives a fresh detail read
    reloaded = ui_page.locator(".panel", has=ui_page.locator(".rulepicker"))
    expect(reloaded.locator("label.toggle-row", has_text="evidence-discipline")
           .locator('input[type="checkbox"]')).to_be_checked()


def test_conversation_header_rule_picker(ui, ui_page):
    """The same picker in the conversation header — the case that motivated it, since a
    conversation shifts topic mid-thread. Binding a rule records the slug and the shared
    endpoint applies it to every reply from here on."""
    import yaml
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Help me restyle the landing page.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    conv_dir = ui.conversations / ui_page.url.rsplit("/", 1)[-1]

    ui_page.locator("details", has_text="⚙ capabilities & budgets").locator("summary").click()
    picker = ui_page.locator(".rulepicker")
    expect(picker).to_be_visible()
    # conversations start with their default set already ticked
    expect(picker.locator("label.toggle-row", has_text="ask-policy")
           .locator('input[type="checkbox"]')).to_be_checked()
    row = picker.locator("label.toggle-row", has_text="interface-design")
    expect(row.locator('input[type="checkbox"]')).not_to_be_checked()
    row.locator('input[type="checkbox"]').check()
    picker.get_by_role("button", name="apply").click()
    expect(_toast(ui_page)).to_contain_text("rules updated")
    held = yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8"))["rules"]
    assert "interface-design" in held


def test_run_waiting_line_names_the_executing_action(ui, ui_page):
    """The bottom "working" line is HONEST about what the run waits on (F170, operator
    note 2026-07-23): after an assistant_action lands with no observation yet, it names
    the executing action ("running util pytest-run…"); once the observation arrives the
    next wait is the model's again."""
    run_dir = ui.seed_run("uiwait", "20260715-160000", "running")
    events = [
        {"type": "assistant_action", "turn": 1,
         "payload": {"kind": "util", "name": "websearch", "say": "searching"}},
        {"type": "observation", "turn": 1,
         "payload": {"kind": "util", "name": "websearch", "exit": 0, "stdout": "ok"}},
        {"type": "assistant_action", "turn": 2,
         "payload": {"kind": "util", "name": "pytest-run", "say": "gating"}},
    ]
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(e) + "\n" for e in events)

    ui_page.goto(f"{ui.url}/#/run/uiwait:20260715-160000")
    waiting = ui_page.locator(".busy", has_text="running util")
    expect(waiting).to_be_visible()
    expect(waiting).to_contain_text("running util pytest-run…")


# ---- F189 / F193 regression flows --------------------------------------------------------


def test_run_page_blocking_question_shows_option_buttons(ui, ui_page):
    """F189: the run view's blocking-question panel (the clarify-dialog surface — the
    wizard's clarify session IS a run page) renders the question's options as buttons,
    and clicking one submits that option one-click."""
    ui.seed_run("uir", "20260715-091000", "waiting_user",
                question={"qid": "q-opt", "question": "Pick a lane?",
                          "options": ["fast", "careful"], "default": "careful",
                          "asked": "20260715-091000"})
    ui.seed_question("uir", "q-opt", "Pick a lane?", mode="blocking",
                     options=["fast", "careful"], default="careful")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-091000")
    panel = ui_page.locator(".panel.warn", has_text="Pick a lane?")
    expect(panel.locator(".answer-opts button")).to_have_count(2)
    panel.get_by_role("button", name="fast", exact=True).click()
    expect(_toast(ui_page)).to_contain_text("answer sent")
    _wait_until((ui.routine_dir("uir") / "inbox" / "answer-q-opt.json").exists)
    answer = json.loads((ui.routine_dir("uir") / "inbox" / "answer-q-opt.json")
                        .read_text(encoding="utf-8"))
    assert answer["text"] == "fast"


def test_decisions_page_access_request_offers_the_four_decisions(ui, ui_page):
    """An access-request record renders the typed decision buttons (allow/deny ×
    now/forever) instead of free-form options; 'allow forever' persists the grant into
    routine.yaml AT CLICK TIME (the web is the one config writer) and files a
    decision-shaped answer the engine can consume."""
    ui.seed_question("uir", "q-req", "May I read the FOO_TOKEN secret?",
                     request=["secret:FOO_TOKEN"])
    ui_page.goto(f"{ui.url}/#/questions")
    panel = ui_page.locator(".question-item", has_text="May I read the FOO_TOKEN")
    expect(panel.locator("code", has_text="secret:FOO_TOKEN")).to_be_visible()
    # a secret request offers "allow once" too since D76 (spent at the next util
    # invocation that receives the var)
    for label in ("allow now", "allow once", "allow forever", "deny now", "never"):
        expect(panel.get_by_role("button", name=label, exact=True)).to_be_visible()
    panel.get_by_role("button", name="allow forever", exact=True).click()
    _wait_until((ui.routine_dir("uir") / "inbox" / "answer-q-req.json").exists)
    answer = json.loads((ui.routine_dir("uir") / "inbox" / "answer-q-req.json")
                        .read_text(encoding="utf-8"))
    assert answer["decision"] == "allow_forever"
    cfg = yaml.safe_load((ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert cfg["grants"] == {"secret:FOO_TOKEN": True}


def test_secret_exposure_panel_refreshes_on_decision(ui, ui_page):
    """F193: a grant decided elsewhere (Decisions-page approval → routine.yaml) must show
    up in the routine page's secret-exposure panel WITHOUT a full reload — the panel
    refetches when the decision's `question_answered` bus event lands."""
    ui.seed_question("uir", "q-sec", "Expose secret FOO_TOKEN to routine 'uir'?",
                     mode="blocking", options=["approve", "decline"])
    ui_page.goto(f"{ui.url}/#/routine/uir")
    expect(ui_page.locator(".panel", has_text="no secrets in the store yet")).to_be_visible()

    # the grant lands in routine.yaml (as the web decision handler persists it) …
    ry = ui.routine_dir("uir") / "routine.yaml"
    cfg = yaml.safe_load(ry.read_text(encoding="utf-8"))
    cfg["grants"] = {"secret:FOO_TOKEN": True}
    ry.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    # … and the decision resolves — the answer's bus event reaches the open page
    r = ui_page.request.post(f"{ui.url}/api/questions/q-sec/answer",
                             headers={"Authorization": "Bearer ui-test-token"},
                             data={"text": "approve"})
    assert r.ok
    row = ui_page.locator('[data-secret-row="FOO_TOKEN"]')
    expect(row).to_be_visible()
    expect(row.locator("select")).to_have_value("true")


# ---- R132 / D70 / R128 flows -------------------------------------------------------------


def test_run_transcript_inline_blocking_approval_strip(ui, ui_page):
    """R132: a BLOCKING util approval is actionable WHERE the user reads — the transcript's
    inline question node carries the one-click approve/decline strip (quick answerForm),
    submitting through the same answer endpoint the Decisions page uses. The pinned panel
    keeps the one full form (F264); the strip has buttons only."""
    run_dir = ui.seed_run("uir", "20260805-090000", "waiting_user",
                          question={"qid": "q-wu", "mode": "blocking",
                                    "question": "Approve revise of global util 'x'?",
                                    "options": ["approve", "decline"],
                                    "type": "util-approval",
                                    "default": "the util is NOT applied until approved",
                                    "asked": "20260805-090000"})
    ui.seed_question("uir", "q-wu", "Approve revise of global util 'x'?", mode="blocking",
                     options=["approve", "decline"],
                     default="the util is NOT applied until approved")
    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "question", "payload": {
            "qid": "q-wu", "mode": "blocking",
            "question": "Approve revise of global util 'x'?",
            "options": ["approve", "decline"], "type": "util-approval",
            "default": "the util is NOT applied until approved"}}) + "\n")

    ui_page.goto(f"{ui.url}/#/run/uir:20260805-090000")
    inline = ui_page.locator(".transcript .ev.question")
    expect(inline).to_be_visible()
    expect(inline).to_contain_text("Approve revise of global util 'x'?")
    # buttons only — the free-text row stays with the pinned panel (F264)
    expect(inline.locator(".answer-opts button")).to_have_count(2)
    assert inline.locator("textarea").count() == 0
    inline.get_by_role("button", name="approve", exact=True).click()
    _wait_until((ui.routine_dir("uir") / "inbox" / "answer-q-wu.json").exists)
    answer = json.loads((ui.routine_dir("uir") / "inbox" / "answer-q-wu.json")
                        .read_text(encoding="utf-8"))
    assert answer["text"] == "approve"
    # settled note: onSuccess writes "✅ answered: approve"; the question_answered bus
    # event may land first with its own phrasing — both start "✅ answered"
    expect(inline).to_contain_text("✅ answered")


def test_new_conversation_composer_folder_access(ui, ui_page):
    """D70: the composer's Folder access section grants fs roots at CREATE time — picked
    with the server-side directory browser, sent as fs_write_roots/fs_read_roots on the
    create request, landing on the conversation's config before the engine boots."""
    data_dir = ui.tmp / "data"
    data_dir.mkdir(exist_ok=True)
    ui_page.goto(f"{ui.url}/#/conversations")
    add = ui_page.get_by_role("button", name="+ add directory…").first   # the read+write editor
    add.wait_for(timeout=10_000)
    add.click()
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill(str(data_dir))
    dlg.locator("input").press("Enter")               # jump to the typed path
    # the jump is async — the empty-dir hint proves the picker LANDED there (selecting
    # before the load resolves would pick the previous directory)
    expect(dlg.locator(".dirpicker-list")).to_contain_text("empty directory")
    dlg.get_by_role("button", name="select this folder").click()
    expect(ui_page.locator(".root-row", has_text=str(data_dir))).to_be_visible()

    ui_page.locator(".conv-new textarea").fill("work on my data folder")
    with ui_page.expect_request(
            lambda r: r.url.rstrip("/").endswith("/api/conversations")
            and r.method == "POST") as req:
        ui_page.get_by_role("button", name="start conversation").click()
    assert str(data_dir) in (req.value.post_data or "")
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rstrip("/").split("/")[-1]
    cfg = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert str(data_dir) in cfg["fs_write_roots"]
    assert str(data_dir) in cfg["fs_read_roots"]      # write grants imply read


def test_conversation_header_folder_access_edit(ui, ui_page):
    """D82: folder access is editable MID-conversation from the ⚙ header panel — a
    directory picked into the write editor saves via PATCH fs_write_roots and lands in
    routine.yaml, where the NEXT reply's boot reads it."""
    extra = ui.tmp / "extra-grant"
    extra.mkdir(exist_ok=True)
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Grant me a folder mid-thread.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    ui_page.locator("summary", has_text="capabilities & budgets").click()
    # two editors in the panel: read first, write second
    add = ui_page.get_by_role("button", name="+ add directory…").nth(1)
    add.wait_for(timeout=10_000)
    add.click()
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill(str(extra))
    dlg.locator("input").press("Enter")               # jump to the typed path
    expect(dlg.locator(".dirpicker-list")).to_contain_text("empty directory")
    dlg.get_by_role("button", name="select this folder").click()
    expect(ui_page.locator(".root-row", has_text=str(extra))).to_be_visible()
    ui_page.get_by_role("button", name="save folder access").click()
    expect(_toast(ui_page)).to_contain_text("folder access saved")
    cfg = yaml.safe_load((ui.conversations / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert str(extra) in cfg["fs_write_roots"]
    assert cfg.get("fs_read_roots") == []             # the read editor was left empty


def test_model_pickers_label_window_sizes(ui, ui_page):
    """R128: the model pickers surface per-model window metadata — the harness model 'm'
    (100k-char default window, 16.4k-token output reservation) labels as a tight window in
    the composer picker (fed by /api/settings/models `window`) and in the conversation
    header's switcher (fed by the detail's `catalog_meta`)."""
    ui_page.goto(f"{ui.url}/#/conversations")
    opt = ui_page.locator('option[value="m"]').first
    opt.wait_for(state="attached", timeout=10_000)
    text = opt.text_content() or ""
    assert "ctx" in text and "tight window" in text

    # the header switcher on an existing conversation carries the same labeling
    ui_page.locator(".conv-new textarea").fill("label check")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    head_opt = ui_page.locator('.conv-model option[value="m"]').first
    head_opt.wait_for(state="attached", timeout=10_000)
    assert "ctx" in (head_opt.text_content() or "")


def test_run_composer_textarea_stacks_on_narrow(ui, ui_page):
    """F346 (user order 2026-08-15): the run composer is ALWAYS a textarea — a message is
    multi-line prose, never a one-line slot — and on a narrow viewport the field is the
    ONLY element on its row: send wraps onto the line beneath it (the F238 full-width
    media rule now covers textareas, and the flex lives in the stylesheet, not inline)."""
    ui.seed_run("uir", "20260815-090000", "running")
    ui_page.set_viewport_size({"width": 420, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:20260815-090000")
    box = ui_page.locator('div.composer textarea[data-persist="run-msg"]')
    expect(box).to_be_visible()
    send = ui_page.locator("div.composer button", has_text="send")
    bb, sb = box.bounding_box(), send.bounding_box()
    assert sb["y"] >= bb["y"] + bb["height"] - 1, \
        f"send must wrap BELOW the full-width message field, got field={bb} send={sb}"
