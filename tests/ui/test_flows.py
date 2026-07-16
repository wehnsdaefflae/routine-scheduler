"""The four safety-net flows (improvement-plan item 3), driven end-to-end in a real
browser against the real app: Decisions answering, the conversation composer, routine-page
saves, and Settings endpoints CRUD. Assertions check BOTH what the user sees (DOM, toast)
and what actually landed on disk — the UI lying about a save is exactly the bug class this
harness exists to catch.
"""

import json

import yaml
from playwright.sync_api import expect


def _toast(page):
    return page.locator("#toast:not([hidden])")


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
    # removing the tag also saves immediately
    ui_page.locator(".tags .tag", has_text="nightly").locator(".x").click()
    expect(ui_page.locator(".tags .tag", has_text="nightly")).to_have_count(0)
    ui_page.wait_for_timeout(200)
    raw = yaml.safe_load(
        (ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["tags"] == []

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
