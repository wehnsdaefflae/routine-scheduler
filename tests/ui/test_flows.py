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

    # an option button prefills the input; typing overrides; Enter submits
    card.get_by_role("button", name="1 · red").click()
    field = card.locator('input[data-persist="answer-q-color"]')
    expect(field).to_have_value("red")
    field.fill("blue, and add a legend")
    field.press("Enter")

    expect(_toast(ui_page)).to_contain_text("answered")
    expect(card.locator(".chip.ok")).to_contain_text("answered · queued")
    answer = json.loads(
        (ui.routine_dir("uir") / "inbox" / "answer-q-color.json").read_text(encoding="utf-8"))
    assert answer["text"] == "blue, and add a legend"
    assert answer["source"] == "web"


def test_decisions_blocking_question_from_live_run(ui, ui_page):
    ui.seed_run("uir", "20260715-090000", "waiting_user",
                question={"qid": "q-go", "question": "Ship it?", "options": [],
                          "default": "", "asked": "20260715-090000"})
    ui_page.goto(f"{ui.url}/#/questions")
    card = ui_page.locator(".question-item.warn")   # blocking questions render loud
    expect(card).to_contain_text("Ship it?")
    card.locator('input[data-persist="answer-q-go"]').fill("yes — ship")
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

    ui_page.goto(f"{ui.url}/#/run/uir:20260715-140000")
    rows = ui_page.locator(".file-row")
    expect(rows).to_have_count(3)
    expect(rows.nth(0)).to_contain_text("state/notes.md")
    expect(rows.nth(0).locator(".file-ops")).to_have_text("read ×2")
    expect(rows.nth(1).locator(".file-ops")).to_have_text("wrote")
    expect(rows.nth(2)).to_have_class("file-row err")
    expect(rows.nth(2).locator(".file-ops")).to_have_text("✕1")


def test_run_view_question_form(ui, ui_page):
    """The run view's blocking-question panel rides the shared answerForm: option buttons
    prefill, the mirrored/Discord note renders, and ask-back sends an intermediate reply."""
    ui.seed_run("uir", "20260715-100000", "waiting_user",
                question={"qid": "q-rv", "question": "Which path?", "options": ["a", "b"],
                          "default": "a", "expires": "2026-07-15T13:00:00+00:00",
                          "mirrored": True, "asked": "20260715-100000"})
    ui.seed_question("uir", "q-rv", "Which path?", mode="blocking", default="a")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-100000")
    box = ui_page.locator(".panel.warn", has_text="Which path?")
    expect(box).to_contain_text("and on Discord")
    expect(box).to_contain_text("without an answer: a")
    box.get_by_role("button", name="a", exact=True).click()
    expect(box.locator("textarea")).to_have_value("a")
    box.locator("textarea").fill("thinking out loud: why not both?")
    box.get_by_role("button", name="ask back").click()
    expect(_toast(ui_page)).to_contain_text("the model will reply and re-ask")
    answer = json.loads(
        (ui.routine_dir("uir") / "inbox" / "answer-q-rv.json").read_text(encoding="utf-8"))
    assert answer["intermediate"] is True
    assert answer["text"] == "thinking out loud: why not both?"


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
    offers continue-this-run vs queue-for-next-run."""
    ui.seed_run("uir", "20260715-110000", "running")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-110000")
    mode = ui_page.locator('select[title="where this message goes"]')
    expect(mode).to_have_value("inject")
    expect(mode).to_be_disabled()
    ui_page.locator('input[placeholder="inject a message into the run…"]').fill("mid-run note")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_be_visible()
    inbox = ui.routine_dir("uir") / "inbox"
    assert any("mid-run note" in m.read_text(encoding="utf-8")
               for m in inbox.glob("msg-*.json"))

    ui.seed_run("uir", "20260715-120000", "finished", summary="done")
    ui_page.goto(f"{ui.url}/#/run/uir:20260715-120000")
    mode = ui_page.locator('select[title="where this message goes"]')
    expect(mode).to_be_enabled()
    expect(mode).to_have_value("converse")          # continuing this run is the default
    mode.select_option("queue")
    ui_page.locator('input[placeholder^="message…"]').fill("for next time")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_contain_text("queued for the next run")
    assert any("for next time" in m.read_text(encoding="utf-8")
               for m in inbox.glob("msg-*.json"))


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
         "payload": {"kind": "util", "name": "websearch", "exit": 0, "stdout": "3 hits"}},
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
    # the injected message renders its reference line as a chip, body clean
    injection = ui_page.locator(".ev.injection")
    expect(injection.locator(".reply-ref")).to_contain_text("turn 1 (util websearch)")
    expect(injection).to_contain_text("user: look deeper")

    # ↩ on turn 1 primes the composer chip (label + the say as snippet)…
    ui_page.locator(".turn .refer-btn").first.click()
    ref = ui_page.locator(".composer-ref")
    expect(ref).to_be_visible()
    expect(ref).to_contain_text("turn 1 (util websearch): Catalog fits — scanning portals.")
    # …and the queued message leads with the quoted reference line
    ui_page.locator('select[title="where this message goes"]').select_option("queue")
    ui_page.locator('input[placeholder^="message…"]').fill("dig into that result")
    ui_page.get_by_role("button", name="send", exact=True).click()
    expect(_toast(ui_page)).to_contain_text("queued for the next run")
    expect(ref).to_be_hidden()                      # sent — the chip clears
    sent = [json.loads(m.read_text(encoding="utf-8"))
            for m in (ui.routine_dir("uir") / "inbox").glob("msg-*.json")]
    assert any(d["text"] == "> re turn 1 (util websearch): Catalog fits — scanning "
                            "portals.\n\ndig into that result" for d in sent)


# ---- 2. Conversation composer ------------------------------------------------------------


def test_conversation_composer(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
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
    messages = list((conv_dir / "inbox").glob("msg-*.json"))
    assert len(messages) == 1
    assert "gym" in messages[0].read_text(encoding="utf-8")


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

    ui_page.goto(ui.url)   # dashboard: the card carries the compact month line
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


# ---- 3c. Library deletes (traits/utils/workflows — permissions + clarify protected) -------


def test_library_delete_flows(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/library")
    editor_panel = ui_page.locator(
        ".panel", has=ui_page.get_by_role("button", name="save + commit"))

    # a trait deletes through the themed dialog; the reload lands on the bare list
    ui_page.get_by_role("link", name="ask-policy", exact=True).click()
    editor_panel.get_by_role("button", name="delete").click()
    _confirm_modal(ui_page, "delete")
    expect(ui_page.get_by_role("link", name="ask-policy", exact=True)).to_have_count(0)
    assert not (ui.tmp / "library" / "traits" / "ask-policy.md").exists()
    assert "#/library" in ui_page.url and "trait/" not in ui_page.url

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
    expect(_toast(ui_page)).to_be_visible()
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


# ---- 5. New-routine setup on the run page (D11) -------------------------------------------


def _seed_clarify_session(ui, draft="Watch arxiv for new agent papers."):
    """A clarification template + one in-flight session, exactly as /api/wizard/start lays
    them down (minus the engine subprocess): the run lives at clarification/runs/<ts>, the
    session recipe in the hidden .wizard-<ts> workspace.
    """
    from rsched.web import wizard_store

    tpl = ui.routines / wizard_store.TEMPLATE_SLUG
    (tpl / "state").mkdir(parents=True, exist_ok=True)
    (tpl / "routine.yaml").write_text(yaml.safe_dump(
        {"name": "Routine clarification", "slug": wizard_store.TEMPLATE_SLUG,
         "enabled": False,
         "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"}}),
        encoding="utf-8")
    wid, ts, d = wizard_store.create_session(ui.server_cfg, draft)
    run_dir = ui.routines / wizard_store.TEMPLATE_SLUG / "runs" / ts
    (run_dir / "transcript.jsonl").write_text(
        f'{{"type": "header", "run_id": "clarification:{ts}"}}\n', encoding="utf-8")
    return wid, ts, d, run_dir


def _set_run_status(run_dir, **patch):
    st = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    st.update(patch)
    (run_dir / "status.json").write_text(json.dumps(st), encoding="utf-8")


def test_new_routine_page_and_setup_banner_link_the_run_page(ui, ui_page):
    """The draft page lists the in-flight session and both it and the global setup banner
    resume onto the clarify run's page — the session surface since D11."""
    _wid, ts, _d, _run_dir = _seed_clarify_session(ui)
    ui_page.goto(f"{ui.url}/#/new-routine")
    expect(ui_page.get_by_role("button", name="start clarification")).to_be_visible()
    inflight = ui_page.locator(".panel.warn", has_text="Setup already in progress")
    expect(inflight).to_contain_text("Watch arxiv")
    expect(inflight.locator("a.btn", has_text="resume"))\
        .to_have_attribute("href", f"#/run/clarification:{ts}")
    banner = ui_page.locator("#setup-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Routine setup in progress")
    expect(banner.locator("a.btn", has_text="resume"))\
        .to_have_attribute("href", f"#/run/clarification:{ts}")


def test_new_routine_draft_is_forgotten_after_start(ui, ui_page):
    """F110: the draft textarea persists across navigation (a half-typed task survives a
    refresh — desired) but must be FORGOTTEN once a clarification starts, so it never refills
    with the last-created routine's task on the next visit (forgetField on a successful start).
    """
    ui_page.goto(f"{ui.url}/#/new-routine")
    ui_page.locator("textarea.code").fill("Watch arxiv for new agent papers.")
    # navigate away and back — the draft SURVIVES (form persistence, the desired behaviour)
    ui_page.goto(f"{ui.url}/#/")
    ui_page.goto(f"{ui.url}/#/new-routine")
    expect(ui_page.locator("textarea.code")).to_have_value("Watch arxiv for new agent papers.")
    # start a clarification — stub the endpoint so no engine subprocess is needed
    ui_page.route("**/api/wizard/start", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"run_id": "clarification:20260719-120000"})))
    ui_page.get_by_role("button", name="start clarification").click()
    # after the start the draft is forgotten: returning shows an EMPTY field, not the old task
    ui_page.goto(f"{ui.url}/#/new-routine")
    expect(ui_page.locator("textarea.code")).to_have_value("")


def test_clarify_run_page_chat_frame_and_question_answer(ui, ui_page):
    """A live clarify run renders on the STANDARD run page with the setup frame mounted,
    and the run page's own question form answers into the .wizard-<ts> workspace inbox —
    the dir the live session polls."""
    _wid, ts, d, run_dir = _seed_clarify_session(ui)
    _set_run_status(run_dir, state="waiting_user", pid=4242,
                    question={"qid": "q-w1", "question": "Daily or weekly?", "type": "text",
                              "options": [], "default": "", "asked": ts})
    ui_page.goto(f"{ui.url}/#/run/clarification:{ts}")
    expect(ui_page.locator(".panel", has_text="New-routine setup")).to_be_visible()
    expect(ui_page.get_by_role("button", name="cancel setup")).to_be_visible()
    field = ui_page.locator('textarea[data-persist="answer-q-w1"]')
    field.fill("weekly")
    field.press("Enter")
    expect(_toast(ui_page)).to_contain_text("answer sent")
    answer = json.loads((d / "inbox" / "answer-q-w1.json").read_text(encoding="utf-8"))
    assert answer["text"] == "weekly"
    assert not (ui.routines / "clarification" / "inbox" / "answer-q-w1.json").exists()


def test_clarify_run_page_suggest_form_after_finish(ui, ui_page, monkeypatch):
    """A finished clarify run with a result flips the panel to the create form — refined
    instruction, workflow picks from the library, and the create button, all on the run
    page (the retired wizard view's suggest stage)."""
    from rsched.web import api_wizard
    from rsched.workflows import suggest as suggest_mod

    monkeypatch.setattr(api_wizard, "suggest_tags",
                        lambda *a, **k: ["papers", "arxiv", "watch"])
    monkeypatch.setattr(suggest_mod, "suggest_traits_permissions",
                        lambda *a, **k: {"traits": [], "permissions": [],
                                         "deliberation": "standard"})
    _wid, ts, d, run_dir = _seed_clarify_session(ui)
    _set_run_status(run_dir, state="finished")
    (d / "state" / "wizard_result.json").write_text(json.dumps({
        "refined_instruction": "Track arxiv agent papers weekly.",
        "suggested_slug": "arxiv-watch", "suggested_name": "Arxiv watch"}), encoding="utf-8")
    ui_page.goto(f"{ui.url}/#/run/clarification:{ts}")
    expect(ui_page.locator("h2", has_text="Refined instruction")).to_be_visible()
    expect(ui_page.locator("textarea.code").first)\
        .to_have_value("Track arxiv agent papers weekly.")
    expect(ui_page.locator(".pick-row .btn").first).to_be_visible()   # library workflow picks
    expect(ui_page.get_by_role("button", name="create routine")).to_be_visible()
    expect(ui_page.locator('input[value="arxiv-watch"]')).to_be_visible()


def test_clarify_run_page_question_form_renders_options(ui, ui_page):
    """A clarify blocking question WITH options renders its option buttons on the run page's
    top question form (F93: 'no options on the new-routine subpage'), and clicking an option
    fills the answer for one-tap submit into the .wizard-<ts> inbox the live session polls."""
    _wid, ts, d, run_dir = _seed_clarify_session(ui)
    _set_run_status(run_dir, state="waiting_user", pid=4242,
                    question={"qid": "q-w3", "question": "How often?", "type": "text",
                              "options": ["daily", "weekly", "monthly"], "default": "weekly",
                              "asked": ts})
    ui_page.goto(f"{ui.url}/#/run/clarification:{ts}")
    opts = ui_page.locator(".answer-opts .btn")
    expect(opts).to_have_count(3)
    opts.filter(has_text="weekly").click()
    field = ui_page.locator('textarea[data-persist="answer-q-w3"]')
    expect(field).to_have_value("weekly")
    field.press("Enter")
    expect(_toast(ui_page)).to_contain_text("answer sent")
    answer = json.loads((d / "inbox" / "answer-q-w3.json").read_text(encoding="utf-8"))
    assert answer["text"] == "weekly"


# ---- 6. Pre-start capabilities & budgets on the composer ----------------------------------


def test_new_conversation_composer_offers_caps_and_budgets(ui, ui_page):
    """The composer carries the SAME ⚙ capabilities & budgets surface as the conversation
    header — a permission granted there (e.g. shell) and a budget set there govern reply #1,
    which fires on create and would miss any post-hoc toggle."""
    import re

    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new summary", has_text="capabilities & budgets").click()
    panel = ui_page.locator(".conv-new")
    expect(panel).to_contain_text("deliberation — thinking on paper")
    expect(panel).to_contain_text("conduct permissions")
    shell_row = panel.locator(".toggle-row").filter(
        has=ui_page.get_by_text("shell", exact=True))
    shell_row.locator('input[type="checkbox"]').check()
    panel.locator('input[title="max tokens per reply (-1 = unlimited)"]').fill("55000")
    panel.locator("textarea").first.fill("Need a shell for this.")
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


# ---- 7. Audit reference links (F63/D14 → the card they name) -------------------------------


def test_audit_refs_link_and_flash(ui, ui_page):
    """D[n]/F[n] mentions in the audit report are hyperlinks to the card they reference;
    following one lands on (and flashes) that card, and the Decisions page's meta items
    carry the same links."""
    import re

    rdir = ui.routines / "self-audit"
    (rdir / "audit").mkdir(parents=True)
    report = {
        "generated": "2026-07-16T20:00:00+00:00",
        "summary": "F1 is carried this run; D1 awaits you.",
        "findings": [{"id": "F1", "severity": "info", "title": "Watch item",
                      "detail": "Blocked on D1."}],
        "decisions": [{"id": "D1", "status": "open", "title": "Pick a path",
                       "detail": "See F1 for the evidence.",
                       "options": ["do it", "leave as-is"]}],
    }
    (rdir / "audit" / "report.json").write_text(json.dumps(report), encoding="utf-8")

    ui_page.goto(f"{ui.url}/#/audit")
    link = ui_page.locator(".panel.prose a.ref-link", has_text="D1")
    expect(link).to_have_attribute("href", "#/audit?focus=D1")
    expect(ui_page.locator("#ref-F1")).to_be_visible()      # findings AND decisions get cards
    expect(ui_page.locator("#ref-D1")).to_contain_text("Pick a path")
    link.click()                                            # follow the ref → land + flash
    expect(ui_page.locator("#ref-D1")).to_have_class(re.compile(r"ref-flash"))

    ui_page.goto(f"{ui.url}/#/questions")                   # the same ids link from the inbox
    card = ui_page.locator(".question-item", has_text="Pick a path")
    flink = card.locator("a.ref-link", has_text="F1")
    expect(flink).to_have_attribute("href", "#/audit?focus=F1")


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


def test_audit_detail_renders_markdown(ui, ui_page):
    """The Audit page (#/audit) renders a report's own prose — finding/decision `detail`
    and the top summary — as real markdown DOM, not literal textContent. Regression (F105,
    2026-07-18): static/views/audit.js never imported md.js, so finding/decision block
    markdown (lists, `code`, tables) showed as raw text — the same gap F104 fixed on the
    Decisions page. Ref-links (F/D mentions) must still work through the md() output."""

    rdir = ui.routines / "self-audit"
    (rdir / "audit").mkdir(parents=True)
    report = {
        "generated": "2026-07-16T20:00:00+00:00",
        "summary": "Carrying F2; D2 is settled.",
        "findings": [{
            "id": "F2", "severity": "improvement", "title": "Render `detail` as markdown",
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

    ui_page.goto(f"{ui.url}/#/audit")
    fcard = ui_page.locator("#ref-F2")
    # the finding's bullet list renders as real <li>, not literal "- lists become …"
    expect(fcard.locator("li", has_text="lists become real")).to_be_visible()
    expect(fcard.locator("code", has_text="code")).to_be_visible()
    assert "- lists become" not in fcard.inner_text()
    # decision detail renders markdown too, and a D-ref inside md() prose still linkifies
    dcard = ui_page.locator("#ref-D2")
    expect(dcard.locator("li", has_text="apply the")).to_be_visible()
    expect(dcard.locator("code", has_text="md()")).to_be_visible()
    expect(dcard.locator("a.ref-link", has_text="F2")).to_have_attribute(
        "href", "#/audit?focus=F2")


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

    ui_page.goto(ui.url)
    card = ui_page.locator(".card", has_text="Test uir")
    strip = card.locator("svg.heartbeat")
    expect(strip).to_be_visible()
    for cls in ("hb-ok", "hb-partial", "hb-failed", "hb-aborted"):
        expect(strip.locator(f"rect.{cls}")).to_have_count(1)
    expect(strip.locator("rect.hb-empty")).to_have_count(11)   # 15 slots, 4 runs
    # newest run (the aborted one) sits at the right edge; its bar opens the run view
    strip.locator("a.hb-bar").last.click()
    expect(ui_page).to_have_url(f"{ui.url}/#/run/uir:20260713-070000")

    ui_page.goto(ui.url)                                       # list view: same strip per row
    ui_page.get_by_role("button", name="☰ list view").click()
    row = ui_page.locator("table.list tbody tr", has_text="Test uir")
    expect(row.locator("svg.heartbeat")).to_be_visible()


def test_routine_page_trait_picker_adds_a_practice_module(ui, ui_page):
    """The post-creation practice picker: ticking a library module and applying copies it
    into the routine's own traits/ VERBATIM and rebuilds main.md's derived Standing-practices
    tail. This is the user's switch — a run can never change its own set."""
    rdir = ui.routines / "uir"
    ui_page.goto(f"{ui.url}/#/routine/uir")
    panel = ui_page.locator(".panel", has=ui_page.locator(".traitpicker"))
    expect(panel).to_be_visible()
    row = panel.locator("label.toggle-row", has_text="evidence-discipline")
    expect(row).to_be_visible()
    row.locator('input[type="checkbox"]').check()
    panel.get_by_role("button", name="apply").click()
    expect(_toast(ui_page)).to_contain_text("practices updated")
    written = (rdir / "traits" / "evidence-discipline.md").read_text(encoding="utf-8")
    assert "# trait: evidence discipline" in written
    assert "tags:" not in written                      # frontmatter stripped, body verbatim
    assert "traits/evidence-discipline.md" in (rdir / "main.md").read_text(encoding="utf-8")

    ui_page.reload()                                   # the tick survives a fresh detail read
    reloaded = ui_page.locator(".panel", has=ui_page.locator(".traitpicker"))
    expect(reloaded.locator("label.toggle-row", has_text="evidence-discipline")
           .locator('input[type="checkbox"]')).to_be_checked()


def test_conversation_header_trait_picker(ui, ui_page):
    """The same picker in the conversation header — the case that motivated it, since a
    conversation shifts topic mid-thread. Adding a module writes it into the conversation's
    own traits/ and the shared endpoint records it for every reply from here on."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Help me restyle the landing page.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    conv_dir = ui.conversations / ui_page.url.rsplit("/", 1)[-1]

    ui_page.locator("details", has_text="⚙ capabilities & budgets").locator("summary").click()
    picker = ui_page.locator(".traitpicker")
    expect(picker).to_be_visible()
    # conversations start with their default set already ticked
    expect(picker.locator("label.toggle-row", has_text="ask-policy")
           .locator('input[type="checkbox"]')).to_be_checked()
    row = picker.locator("label.toggle-row", has_text="interface-design")
    expect(row.locator('input[type="checkbox"]')).not_to_be_checked()
    row.locator('input[type="checkbox"]').check()
    picker.get_by_role("button", name="apply").click()
    expect(_toast(ui_page)).to_contain_text("practices updated")
    assert (conv_dir / "traits" / "interface-design.md").is_file()
