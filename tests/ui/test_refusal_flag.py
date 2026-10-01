"""⚑ flag-as-refusal in the REAL console (operator decision 2026-10-01): every reply carries
it beside ⟲ and ⑂; clicking it opens the warning gate, which quotes the message the server
will re-send (the real GET preview, over a real transcript), names the model that takes over
— or asks for one when the conversation has none — and says what is NOT undone. Cancel posts
nothing; confirm posts the reply's turn AND its own timestamp. The server-side cut, model
switch and re-send are pinned in tests/test_refusal_flag.py.
"""

from __future__ import annotations

import json
import re

import yaml
from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .conftest import until

TS = "20261001-090000"
REPLY2_TS = "2026-10-01T09:05:00+00:00"
ASK = "Now make the letter angrier."
REFUSED = "That letter would read as harassment, so I would rather not write it."

EVENTS = [
    {"type": "header", "run_id": "X", "routine": "X",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 9, "out": 1, "model": "dummy/m"},
     "payload": {"kind": "finish", "say": "done", "status": "ok", "summary": "First draft."}},
    {"ts": "2026-10-01T09:01:00+00:00", "type": "finish", "turns": 1,
     "usage_total": {"in": 9, "out": 1},
     "payload": {"status": "ok", "summary": "Here is the first draft.", "authored": True}},
    {"ts": "t", "type": "user_injection",
     "payload": {"text": "this run already ENDED (status ok)", "source": "engine",
                 "replay": False}},
    {"ts": "t", "type": "user_injection", "payload": {"text": ASK}},
    {"type": "assistant_action", "turn": 2, "usage": {"in": 9, "out": 1, "model": "dummy/m"},
     "payload": {"kind": "finish", "say": "declining", "status": "ok", "summary": REFUSED}},
    {"ts": REPLY2_TS, "type": "finish", "turns": 2, "usage_total": {"in": 18, "out": 2},
     "payload": {"status": "ok", "summary": REFUSED, "authored": True}},
]


def _conversation(ui, ui_page, *, uncensored: str | None = "m", events=EVENTS,
                  state: str = "finished"):
    """A started conversation with a finished two-reply run, the second reply a refusal."""
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Draft the letter.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    conv_dir = ui.conversations / slug
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    raw["models"] = {"main": "m", **({"uncensored": uncensored} if uncensored else {})}
    (conv_dir / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    run_dir = conv_dir / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": state, "turn": 2})
    return slug, run_dir


def _record_posts(ui_page) -> list:
    """Answer the flag POST in the browser (the server half is tests/test_refusal_flag.py) and
    let the GET preview through to the real server — the gate must quote ITS answer."""
    posted: list = []

    def handle(route):
        if route.request.method != "POST":
            route.continue_()
            return
        posted.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "model": "m", "archive": "rewind-x.jsonl"}))
    ui_page.route(re.compile(r".*/flag-refusal(\?.*)?$"), handle)
    return posted


def _refused_reply(ui_page):
    reply = ui_page.locator(".msg.assistant", has_text="rather not write it")
    expect(reply).to_be_visible()
    return reply


def test_every_reply_carries_the_flag_and_no_user_message_does(ui, ui_page):
    _conversation(ui, ui_page)
    ui_page.reload()
    expect(_refused_reply(ui_page).locator(".flag-msg")).to_have_attribute("data-flag-turn", "2")
    expect(ui_page.locator(".msg.assistant .flag-msg")).to_have_count(2)
    expect(ui_page.locator(".msg.user .flag-msg")).to_have_count(0)


def test_the_gate_says_what_happens_and_cancel_changes_nothing(ui, ui_page):
    _, run_dir = _conversation(ui, ui_page)
    before = (run_dir / "transcript.jsonl").read_bytes()
    posted = _record_posts(ui_page)
    ui_page.reload()
    _refused_reply(ui_page).locator(".flag-msg").click()
    gate = ui_page.get_by_role("dialog")
    expect(gate).to_be_visible()
    expect(gate).to_contain_text("This reply and every reply after it are discarded")
    expect(gate).to_contain_text("reversible by hand")
    expect(gate.locator(".flag-quote")).to_have_text(ASK)     # the server's re-send, verbatim
    expect(gate).to_contain_text("m answers it, and becomes this conversation's main model "
                                 "from now on")
    expect(gate).to_contain_text("files written, messages sent")
    expect(gate).to_contain_text("NOT undone")
    gate.get_by_role("button", name="cancel").click()
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
    ui_page.wait_for_timeout(400)
    assert posted == []
    assert (run_dir / "transcript.jsonl").read_bytes() == before


def test_confirm_posts_the_reply_by_its_turn_and_its_own_timestamp(ui, ui_page):
    _conversation(ui, ui_page)
    posted = _record_posts(ui_page)
    ui_page.reload()
    _refused_reply(ui_page).locator(".flag-msg").click()
    ui_page.get_by_role("dialog").get_by_role("button", name="flag & re-send").click()
    until(lambda: posted, what="the flag POST", page=ui_page)
    assert posted == [{"turn": 2, "ts": REPLY2_TS}]
    expect(ui_page.locator("#toast")).to_contain_text("m takes over")


def test_with_no_uncensored_model_the_gate_asks_for_the_one_that_takes_over(ui, ui_page):
    _conversation(ui, ui_page, uncensored=None)
    posted = _record_posts(ui_page)
    ui_page.reload()
    _refused_reply(ui_page).locator(".flag-msg").click()
    gate = ui_page.get_by_role("dialog")
    expect(gate).to_contain_text("This conversation has no uncensored model")
    expect(gate).to_contain_text("uncensored and main model from now on")
    confirm = gate.get_by_role("button", name="flag & re-send")
    expect(confirm).to_be_disabled()                          # nothing chosen yet
    gate.locator("select.flag-pick").select_option("m")       # the conversation's catalog
    expect(confirm).to_be_enabled()
    confirm.click()
    until(lambda: posted, what="the flag POST", page=ui_page)
    assert posted == [{"turn": 2, "ts": REPLY2_TS, "model": "m"}]


def test_a_reply_in_flight_cannot_be_flagged(ui, ui_page):
    _conversation(ui, ui_page, state="running")
    posted = _record_posts(ui_page)
    ui_page.reload()
    _refused_reply(ui_page).locator(".flag-msg").click()
    expect(ui_page.locator("#toast")).to_contain_text("mid-reply")
    expect(ui_page.locator(".modal-overlay")).to_have_count(0)
    assert posted == []


def test_the_flag_stands_in_the_chat_between_the_replies(ui, ui_page):
    """After a flag the engine records it (seam `operator`) just before the re-sent message:
    a fact of the conversation, so it renders at chat level — never folded into a reply's
    work, where the automatic seams' refusal records go."""
    flagged = [*EVENTS[:3],
               {"ts": "t", "type": "refusal",
                "payload": {"where": "operator", "turn": 2, "model": "m",
                            "takeover_model": "unc", "message": REFUSED,
                            "archive": "rewind-x.jsonl"}},
               {"ts": "t", "type": "user_injection", "payload": {"text": ASK}}]
    _conversation(ui, ui_page, events=flagged)
    ui_page.reload()
    line = ui_page.locator(".chat > .ev.refusal.operator-flag")
    expect(line).to_contain_text("you flagged the reply at turn 2 (m) as a refusal")
    expect(line).to_contain_text("unc carries the conversation from here")
