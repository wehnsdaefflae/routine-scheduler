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
    assert "vllm" not in cfg["endpoints"]
    assert "llama" not in cfg.get("models", {})
