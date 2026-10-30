"""F807/R3060: a blocking question carrying a `config_patch` is ANSWERED AND APPLIED in the
conversation it was asked in.

A routine filed a blocking `ask_user` with a `config_patch` (added Gmail OAuth scopes) and the
operator reported he could only answer it on the Decisions page, not inline in the conversation
he was in, and asked why. The run was waiting there and he was reading there.

The pinned panel above the composer (`static/components/answerform.js` `questionPanel`, fed by
the state stream's status.json question) is the card the operator reads while a run blocks. It
rendered the question and the answer form and nothing about the proposal — because the live
question published into status.json carried no patch at all (`engine/interact.py`), so no
surface could offer the one control that applies it. Both halves are pinned: the engine's, in
`tests/test_blocking_patch_surface.py`; the operator's, here, by loading what he sees.
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import expect

from rsched.paths import atomic_write_json, read_yaml

from .conftest import until
from .helpers import start_conversation

ASK = "Read my newsletters and digest them."
TS = "20261029-080001"
QUESTION = "Add the Gmail OAuth scopes the digest needs?"


def _block_on_a_patch(conv_dir, slug: str, *, patch: dict, target: str = "") -> None:
    """Park the conversation's run on a blocking question carrying `patch`, exactly as
    `interact.handle_ask` leaves it: the durable record in questions/pending, and the live
    copy in status.json that every surface renders while the run waits."""
    question = {"qid": "q-20261029-080001-1", "mode": "blocking", "question": QUESTION,
                "options": [], "type": "question", "asked": TS, "routine": slug,
                "config_patch": patch, **({"config_target": target} if target else {}),
                "conversation": True}
    pending = conv_dir / "questions" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    atomic_write_json(pending / f"{question['qid']}.json", question)
    run_dir = conv_dir / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": f"{slug}:{TS}", "routine": slug, "depth": 0})
        + "\n"
        + json.dumps({"type": "question", "turn": 1, "payload": question}) + "\n",
        encoding="utf-8")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{slug}:{TS}", "state": "waiting_user", "turn": 1,
                       "question": question})


def test_the_chat_question_card_applies_the_patch_it_proposes(ui, ui_page):
    """The whole of R3060: the card where the question was asked carries the proposal and the
    one control that applies it, and the control WORKS — no second page, no hunting.

    Loads what the operator sees (the rendered panel), not an attribute pointing at it.
    """
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    _block_on_a_patch(conv_dir, slug, patch={"budgets": {"max_turns": 120}}, target="uir")
    ui_page.reload()

    # NOT `.panel.warn`: a successful apply drops the blocking tint (`clearWarn`), so a
    # locator pinned to that class stops matching the very card it is asserting on — which is
    # how this test first failed. The question text is what identifies the panel either way.
    panel = ui_page.locator(".panel", has_text=QUESTION)
    expect(panel).to_be_visible()
    expect(panel).to_have_class(re.compile(r"\bwarn\b"))     # a blocking question waits on you
    # the proposal is shown, and names the routine it would change (D123/F458)
    expect(panel).to_contain_text("proposed config change for uir")
    expect(panel).to_contain_text("max_turns")
    # …and the honesty line, because the form beside it does NOT apply anything
    expect(panel).to_contain_text("answering never applies it")

    apply_btn = panel.get_by_role("button", name="approve & apply")
    expect(apply_btn).to_be_visible()
    apply_btn.click()
    expect(panel).to_contain_text("the config change was applied")
    expect(panel).not_to_have_class(re.compile(r"\bwarn\b"))  # taken: nothing waits on it now
    until(lambda: read_yaml(ui.routines / "uir" / "routine.yaml")
          .get("budgets", {}).get("max_turns") == 120, what="the patch applied from the chat")


def test_a_blocking_question_without_a_patch_grows_no_apply_control(ui, ui_page):
    """The panel every other blocking question gets is unchanged: a question with nothing to
    apply must not sprout a control over a proposal that does not exist."""
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    question = {"qid": "q-20261029-080001-2", "mode": "blocking", "question": "Which folder?",
                "options": [], "type": "question", "asked": TS, "routine": slug}
    run_dir = conv_dir / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": f"{slug}:{TS}", "routine": slug, "depth": 0})
        + "\n", encoding="utf-8")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{slug}:{TS}", "state": "waiting_user", "turn": 1,
                       "question": question})
    ui_page.reload()

    panel = ui_page.locator(".panel.warn", has_text="Which folder?")
    expect(panel).to_be_visible()
    expect(panel.get_by_role("button", name="approve & apply")).to_have_count(0)
    expect(panel).not_to_contain_text("proposed config change")
