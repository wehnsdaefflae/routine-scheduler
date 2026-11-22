"""A hand-back lands as a CARD, and a conversation with no family reads no lineage (D306 pts 3
and 5). Both are about what the parent conversation's view DOES with a branch: it renders a
delivered result as the child's, not as the person's own words, and it stops paying for a
lineage read that every conversation used to make.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.paths import atomic_write_json

from .helpers import start_conversation

ASK = "Weigh the two options."

#: header, one complete turn, and the finished REPLY a per-message fork control reads.
REPLY_EVENTS = [
    {"type": "header", "run_id": "X", "routine": "X",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 9, "out": 1},
     "payload": {"kind": "read_file", "say": "orienting", "path": "state/plan.md"}},
    {"type": "observation", "turn": 1, "payload": {"kind": "read_file", "content": "PLAN"}},
    {"type": "finish", "turns": 1, "usage_total": {"in": 18, "out": 2},
     "payload": {"status": "ok", "summary": "Option B, on the cost curve."}},
]


def _run(conv_dir, events, ts="20260827-100000"):
    run_dir = conv_dir / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 1})


def _handback_event(paths):
    """The delivered hand-back as the engine records it: the drained inbox message becomes a
    `user_injection` carrying its `via` and the `handback` record `branches.hand_back` filed
    through `inbox.file_message`'s `extra`.
    """
    return {"type": "user_injection", "ts": "2026-08-27T11:00:00+00:00",
            "payload": {"text": "[branch handed back] The branch \u201cCost curve\u201d (c-b1) "
                                "handed its result back to you.\n\noption B wins, because of "
                                "the cost curve\n\nCollected into your artifacts/: "
                                + ", ".join(paths),
                        "via": "branch",
                        "handback": {"slug": "c-b1", "name": "Cost curve",
                                     "paths": list(paths)}}}


def test_a_branch_hand_back_renders_as_a_card_with_its_files_not_as_a_user_bubble(ui, ui_page):
    """D306 pt 3: `hand_back` files the delivery with `via="branch"` and the chat ignored
    `via` — so the parent showed a branch's conclusion as the operator's OWN words, the copied
    files named only inside that prose. It renders as a hand-back card naming the branch,
    linking to it, and carrying each landed file as a link into the artifacts panel.
    """
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    rel = f"artifacts/from-branch-{slug}-b1/verdict.md"
    _run(conv_dir, [*REPLY_EVENTS, _handback_event([rel])])
    (conv_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (conv_dir / rel).write_text("option B wins", encoding="utf-8")
    ui_page.reload()

    card = ui_page.locator(".handback-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("handed back")
    expect(card).to_contain_text("Cost curve")
    expect(card).to_contain_text("option B wins, because of the cost curve")
    expect(card.locator("a[href='#/conversations/c-b1']")).to_have_count(1)
    # the landed file is a LINK built from the record — not a name read out of the prose
    chip = card.locator(".attach-chip")
    expect(chip).to_have_count(1)
    expect(chip).to_have_attribute("title", rel)
    # …and the delivery is NOT rendered as the person's own message
    expect(ui_page.locator(".msg.user", has_text="handed back")).to_have_count(0)
    chip.click()
    expect(ui_page.locator(".art-viewer")).to_contain_text("option B wins")


def test_a_conversation_with_no_family_makes_no_lineage_request(ui, ui_page):
    """D306 pt 5: `/lineage` reads EVERY conversation's routine.yaml to find children, and the
    header asked for it on mount for every conversation view — paid in full by the overwhelming
    majority, which have neither a parent nor a branch. The view derives both from the
    conversation LIST rows it already reads, and asks the route only when one holds.
    """
    asked: list[str] = []
    ui_page.route("**/lineage", lambda route: (asked.append(route.request.url),
                                               route.continue_()))
    slug, conv_dir = start_conversation(ui, ui_page, ASK)
    _run(conv_dir, REPLY_EVENTS)
    ui_page.reload()
    reply = ui_page.locator(".msg.assistant", has_text="Option B, on the cost curve")
    expect(reply).to_be_visible()
    ui_page.wait_for_timeout(600)
    assert asked == [], asked

    # …and a conversation that HAS a family still reads it: the fork lands on the branch, whose
    # own list row carries `parent`, and its header names where it came from.
    reply.locator(".branch-msg").click()
    ui_page.wait_for_url(f"**/conversations/{slug}-b1")
    expect(ui_page.locator(".conv-head-row").first).to_contain_text("branched from")
    assert asked, "a branch must still read its lineage"
