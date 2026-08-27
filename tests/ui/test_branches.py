"""Conversation branching in the REAL console (F325): the ⑂ branch control forks a thread at a
turn, the branch shows its lineage and grows a ↩ hand-back, and the hand-back delivers a summary
plus artefacts into the parent's inbox — without touching the parent's transcript.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from rsched.paths import atomic_write_json

# header + one complete turn + a later turn that must NOT reach the branch
EVENTS = [
    {"type": "header", "run_id": "X", "routine": "X",
     "workflow": {"slug": "converse", "commit": "abc", "version": 3}, "depth": 0},
    {"type": "assistant_action", "turn": 1, "usage": {"in": 9, "out": 1},
     "payload": {"kind": "read_file", "say": "orienting", "path": "state/plan.md"}},
    {"type": "observation", "turn": 1, "payload": {"kind": "read_file", "content": "PLAN"}},
    {"type": "assistant_action", "turn": 2, "usage": {"in": 9, "out": 1},
     "payload": {"kind": "write_file", "say": "AFTER-THE-FORK", "path": "state/late.md"}},
    {"type": "observation", "turn": 2, "payload": {"kind": "write_file", "ok": True}},
]


def _start_conversation(ui, ui_page, text="Weigh the two options."):
    ui_page.goto(f"{ui.url}/#/conversations")
    ui_page.locator(".conv-new textarea").fill(text)
    ui_page.get_by_role("button", name="start conversation").click()
    ui_page.wait_for_url("**/conversations/**")
    slug = ui_page.url.rsplit("/", 1)[-1]
    return slug, ui.conversations / slug


def _finished_run(conv_dir, ts="20260827-100000"):
    """A terminal run with a real two-turn transcript — what a fork point needs to exist."""
    run_dir = conv_dir / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})


def _answer_modal(ui_page, value):
    """The console replaced every native prompt() with a themed modal, so a branch dialog is
    driven like any other panel — not through page.on("dialog")."""
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill(value)
    dlg.get_by_role("button", name="ok").click()


def _fork_at(ui_page, turn=1):
    ui_page.get_by_role("button", name="⑂ branch").click()
    _answer_modal(ui_page, str(turn))


def test_branch_button_forks_and_opens_the_branch_with_its_lineage(ui, ui_page):
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    ui_page.reload()

    _fork_at(ui_page, 1)
    # the console navigates to the new branch
    ui_page.wait_for_url(f"**/conversations/{slug}-b1")
    head = ui_page.locator(".conv-head-row").first
    expect(head).to_contain_text("branched from")
    expect(head).to_contain_text("at turn 1")

    branch_dir = ui.conversations / f"{slug}-b1"
    evs = [json.loads(x) for x in
           (next((branch_dir / "runs").iterdir()) / "transcript.jsonl").read_text().splitlines()]
    assert [e["type"] for e in evs] == ["header", "assistant_action", "observation"]
    assert not any("AFTER-THE-FORK" in json.dumps(e) for e in evs)
    # the parent is untouched — the whole reason a fork copies
    parent_evs = (conv_dir / "runs" / "20260827-100000" / "transcript.jsonl").read_text()
    assert parent_evs.count("\n") == len(EVENTS)


def test_parent_lists_its_branches_and_has_no_hand_back(ui, ui_page):
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    ui_page.reload()
    _fork_at(ui_page, 1)
    ui_page.wait_for_url(f"**/conversations/{slug}-b1")

    # back on the parent: it names the branch, and offers no hand-back (it has no parent)
    ui_page.goto(f"{ui.url}/#/conversations/{slug}")
    head = ui_page.locator(".conv-head-row").first
    expect(head).to_contain_text("1 branch")
    expect(ui_page.get_by_role("button", name="↩ hand back")).to_be_hidden()
    expect(ui_page.get_by_role("button", name="⑂ branch")).to_be_visible()


def test_hand_back_delivers_summary_and_artifacts_to_the_parent(ui, ui_page):
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    ui_page.reload()
    _fork_at(ui_page, 1)
    ui_page.wait_for_url(f"**/conversations/{slug}-b1")

    branch_dir = ui.conversations / f"{slug}-b1"
    (branch_dir / "artifacts").mkdir(exist_ok=True)
    (branch_dir / "artifacts" / "verdict.md").write_text("option B wins", encoding="utf-8")

    back = ui_page.get_by_role("button", name="↩ hand back")
    expect(back).to_be_visible()
    back.click()
    _answer_modal(ui_page, "option B wins, because of the cost curve")
    expect(ui_page.locator("#toast")).to_contain_text("handed back")

    msgs = list((conv_dir / "inbox").glob("msg-branch-*.json"))
    assert len(msgs) == 1
    text = json.loads(msgs[0].read_text())["text"]
    assert "option B wins, because of the cost curve" in text
    assert "not a merge" in text          # the parent is told what it is receiving
    landed = conv_dir / "artifacts" / f"from-branch-{slug}-b1" / "verdict.md"
    assert landed.read_text() == "option B wins"


def test_branch_refuses_while_a_reply_is_live(ui, ui_page):
    """The fork point must be a settled turn; mid-reply the transcript is still growing."""
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    atomic_write_json(conv_dir / "runs" / "20260827-100000" / "status.json",
                      {"state": "running", "turn": 2})
    ui_page.reload()
    ui_page.get_by_role("button", name="⑂ branch").click()
    expect(ui_page.locator("#toast")).to_contain_text("mid-reply")
    assert not (ui.conversations / f"{slug}-b1").exists()
