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

    raw = yaml.safe_load(
        (ui.routine_dir("uir") / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["description"] == "A sharper one-line description."
    assert raw["budgets"]["max_turns"] == 42


# ---- 4. Settings endpoints CRUD ----------------------------------------------------------


def _server_yaml(ui) -> dict:
    return yaml.safe_load((ui.tmp / "config.yaml").read_text(encoding="utf-8"))


def test_settings_endpoints_crud(ui, ui_page):
    ui_page.on("dialog", lambda d: d.accept())   # the delete confirms
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

    # DELETE the model, then the endpoint (each behind a confirm dialog).
    # .last = the INNERMOST matching panel (the card), not the section wrapper around it.
    model_card = ui_page.locator(".panel",
                                 has=ui_page.locator("strong", has_text="llama")).last
    model_card.get_by_role("button", name="delete").click()
    expect(ui_page.locator("strong", has_text="llama")).to_have_count(0)
    endpoint_card = ui_page.locator(".panel",
                                    has=ui_page.locator("strong", has_text="vllm")).first
    endpoint_card.get_by_role("button", name="delete").click()
    expect(ui_page.locator("strong", has_text="vllm")).to_have_count(0)
    cfg = _server_yaml(ui)
    assert "vllm" not in cfg["endpoints"]
    assert "llama" not in cfg.get("models", {})
