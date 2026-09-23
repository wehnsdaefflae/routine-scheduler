"""The chat echo bubble's own controls: revise and withdraw a queued message (D139).

The operator's ask was "i wanna see that message and be able to delete / revise it until it is
consumed by the model". A ROUTINE's inbox always allowed exactly that; a CONVERSATION — the
surface messages are most often injected on — had only POST, and the optimistic echo bubble
(F295) carried no id, so there was nothing to address even in principle.

These flows drive the REAL console: the bubble that says "sent" is where the message is
rewritten or taken back, and the inbox file on disk is the proof, because that file is what the
model will actually read.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect


def _start_conversation(ui, ui_page):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill("Plan the trip.")
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    return slug, ui.conversations / slug


def _queued(conv_dir):
    """What the model would read: the still-queued conversation messages, oldest first."""
    out = []
    for p in sorted((conv_dir / "inbox").glob("msg-*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("via") == "conversation":
            out.append((p.stem, rec))
    return out


def test_a_queued_message_can_be_revised_from_its_own_bubble(ui, ui_page):
    """The revised text replaces the old one in the SAME file, so the message keeps its
    place in the queue and the model reads one message, not two."""
    _slug, conv_dir = _start_conversation(ui, ui_page)

    composer = ui_page.locator(".conv-view textarea").last
    composer.fill("book teh 9:40 train")
    ui_page.get_by_role("button", name="send").click()

    bubble = ui_page.locator(".msg.user.pending")
    expect(bubble).to_be_visible()
    queued = _queued(conv_dir)
    assert len(queued) == 1 and "teh 9:40" in queued[0][1]["text"]
    msg_id = queued[0][0]

    bubble.get_by_role("button", name="revise").click()
    box = bubble.locator("textarea.pending-edit")
    expect(box).to_be_visible()
    box.fill("book the 9:40 train")
    bubble.get_by_role("button", name="save").click()

    expect(ui_page.locator(".msg.user.pending")).to_contain_text("the 9:40 train")
    after = _queued(conv_dir)
    assert len(after) == 1, "a revision rewrites the message, it never adds a second one"
    assert after[0][0] == msg_id, "same file, so the queue position holds"
    assert after[0][1]["text"] == "book the 9:40 train"
    assert after[0][1]["edited"], "the revision is stamped"


def test_a_queued_message_can_be_withdrawn_before_the_model_reads_it(ui, ui_page):
    _slug, conv_dir = _start_conversation(ui, ui_page)

    composer = ui_page.locator(".conv-view textarea").last
    composer.fill("never mind this one")
    ui_page.get_by_role("button", name="send").click()

    bubble = ui_page.locator(".msg.user.pending")
    expect(bubble).to_be_visible()
    assert len(_queued(conv_dir)) == 1

    bubble.get_by_role("button", name="withdraw").click()

    expect(ui_page.locator(".msg.user.pending")).to_have_count(0)
    assert _queued(conv_dir) == [], "the delivery is gone — the model never sees it"
