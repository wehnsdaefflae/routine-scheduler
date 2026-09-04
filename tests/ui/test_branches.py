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
        "".join(json.dumps(e) + "\n" for e in REPLY_EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})


def _answer_modal(ui_page, value):
    """The console replaced every native prompt() with a themed modal, so a branch dialog is
    driven like any other panel — not through page.on("dialog")."""
    dlg = ui_page.locator(".modal-overlay")
    expect(dlg).to_be_visible()
    dlg.locator("input").fill(value)
    dlg.get_by_role("button", name="ok").click()


def _fork_at(ui_page):
    """Fork via the per-message control on the reply — the only fork path since the header
    ⑂ branch button was removed (D113). The fork point is the reply's own turn (2 here)."""
    reply = ui_page.locator(".msg.assistant", has_text="Option B, on the cost curve")
    expect(reply).to_be_visible(timeout=10_000)
    reply.locator(".branch-msg").click()


def test_branch_button_forks_and_opens_the_branch_with_its_lineage(ui, ui_page):
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    ui_page.reload()

    _fork_at(ui_page)
    # the console navigates to the new branch
    ui_page.wait_for_url(f"**/conversations/{slug}-b1")
    head = ui_page.locator(".conv-head-row").first
    expect(head).to_contain_text("branched from")
    expect(head).to_contain_text("at turn 2")

    branch_dir = ui.conversations / f"{slug}-b1"
    evs = [json.loads(x) for x in
           (next((branch_dir / "runs").iterdir()) / "transcript.jsonl").read_text().splitlines()]
    assert [e["type"] for e in evs] == [
        "header", "assistant_action", "observation", "assistant_action", "observation"]
    # the branch inherits THROUGH the forked reply (turn 2) and stops before its finish
    assert not any(e["type"] == "finish" for e in evs)
    # the parent is untouched — the whole reason a fork copies
    parent_evs = (conv_dir / "runs" / "20260827-100000" / "transcript.jsonl").read_text()
    assert parent_evs.count("\n") == len(REPLY_EVENTS)


def test_parent_lists_its_branches_and_has_no_hand_back(ui, ui_page):
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    ui_page.reload()
    _fork_at(ui_page)
    ui_page.wait_for_url(f"**/conversations/{slug}-b1")

    # back on the parent: it names the branch, and offers no hand-back (it has no parent)
    ui_page.goto(f"{ui.url}/#/conversations/{slug}")
    head = ui_page.locator(".conv-head-row").first
    expect(head).to_contain_text("1 branch")
    expect(ui_page.get_by_role("button", name="↩ hand back")).to_be_hidden()
    # D113: the header carries NO fork button anymore — forking is per-message only
    expect(ui_page.get_by_role("button", name="⑂ branch", exact=True)).to_have_count(0)
    expect(ui_page.locator(".msg.assistant .branch-msg")).to_have_count(1)


def test_hand_back_delivers_summary_and_artifacts_to_the_parent(ui, ui_page):
    slug, conv_dir = _start_conversation(ui, ui_page)
    _finished_run(conv_dir)
    ui_page.reload()
    _fork_at(ui_page)
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
    reply = ui_page.locator(".msg.assistant", has_text="Option B, on the cost curve")
    expect(reply).to_be_visible(timeout=10_000)
    reply.locator(".branch-msg").click()
    expect(ui_page.locator("#toast")).to_contain_text("mid-reply")
    assert not (ui.conversations / f"{slug}-b1").exists()


# A conversation with a finished REPLY — a finish event carrying the turn it ran on, which is
# what a per-message fork control reads. The plain EVENTS above stop before one.
REPLY_EVENTS = [
    *EVENTS,
    {"type": "finish", "turns": 2, "usage_total": {"in": 18, "out": 2},
     "payload": {"status": "ok", "summary": "Option B, on the cost curve."}},
]


def test_a_reply_carries_a_branch_from_here_control_that_needs_no_turn_number(ui, ui_page):
    """R1006: forking is "fork AT a turn", but the only control was in the conversation HEADER
    behind a prompt asking the user to TYPE that turn — a number they had to go and count. A
    reply is itself a clean turn boundary, so the reply carries the control and the number is
    implied by which one was clicked. The header entry point has since been removed (D113):
    forking is a per-message act, never a typed turn number.
    """
    slug, conv_dir = _start_conversation(ui, ui_page)
    run_dir = conv_dir / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in REPLY_EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})
    ui_page.reload()

    reply = ui_page.locator(".msg.assistant", has_text="Option B, on the cost curve")
    expect(reply).to_be_visible(timeout=10_000)
    fork = reply.locator(".branch-msg")
    # the fork point is the reply's own turn — no modal, no typed number
    expect(fork).to_have_attribute("data-branch-turn", "2")
    fork.click()

    ui_page.wait_for_url(f"**/conversations/{slug}-b1")
    expect(ui_page.locator(".conv-head-row").first).to_contain_text("at turn 2")
    branch_run = next((ui.conversations / f"{slug}-b1" / "runs").iterdir())
    evs = [json.loads(x) for x in
           (branch_run / "transcript.jsonl").read_text().splitlines()]
    # through turn 2 — the branch inherits the reply it was forked from — and stops there
    assert [e["type"] for e in evs] == [
        "header", "assistant_action", "observation", "assistant_action", "observation"]
    assert not any(e["type"] == "finish" for e in evs)


def test_a_user_message_carries_no_fork_control(ui, ui_page):
    """Only a REPLY is a turn boundary. A user message sits between turns, so offering a fork
    on it would have to invent a fork point — the API refuses one that is not in the transcript.
    """
    _slug, conv_dir = _start_conversation(ui, ui_page, text="Weigh the two options.")
    run_dir = conv_dir / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in REPLY_EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})
    ui_page.reload()

    expect(ui_page.locator(".msg.assistant .branch-msg")).to_have_count(1, timeout=10_000)
    expect(ui_page.locator(".msg.user .branch-msg")).to_have_count(0)


def test_a_reply_carries_a_rewind_to_here_control_that_posts_the_reply_turn(ui, ui_page):
    """F416 (operator msg 2026-09-01): rewind used to live ONLY in the run view's ⟲ control,
    behind a prompt asking the user to TYPE a turn. A reply is a clean turn boundary, so — like
    the ⑂ branch control (R1006) — each reply now carries a per-message ⟲ rewind whose cut point
    is its own turn. Clicking it confirms, then POSTs /rewind with that turn (the server-side
    truncate + re-open is covered in test_api.py::test_rewind_run_endpoint).
    """
    _slug, conv_dir = _start_conversation(ui, ui_page)
    run_dir = conv_dir / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in REPLY_EVENTS), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})

    posted = {}

    def handle(route):
        posted["body"] = json.loads(route.request.post_data or "{}")
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "kept_through_turn": posted["body"].get("turn"),
                                       "archive": "rewind-x.jsonl"}))
    ui_page.route("**/rewind", handle)
    ui_page.reload()

    reply = ui_page.locator(".msg.assistant", has_text="Option B, on the cost curve")
    expect(reply).to_be_visible(timeout=10_000)
    rewind = reply.locator(".rewind-msg")
    expect(rewind).to_have_attribute("data-rewind-turn", "2")   # the reply's own turn — no prompt
    rewind.click()
    # confirmDialog (destructive): a themed modal whose confirm button is labelled "rewind"
    ui_page.locator(".modal-overlay").get_by_role("button", name="rewind", exact=True).click()
    ui_page.wait_for_timeout(400)   # < the 800ms reload; the POST body is the real contract
    assert posted.get("body") == {"turn": 2}, posted


def test_agent_reply_can_target_an_earlier_message(ui, ui_page):
    """D117 (operator msg-8): the agent's reply may target an earlier message — a finish event
    carrying `reply_to` renders a ↩ reference chip above the reply, exactly the way a user's own
    reply-to-a-message renders (`.reply-ref`)."""
    _slug, conv_dir = _start_conversation(ui, ui_page)
    run_dir = conv_dir / "runs" / "20260827-100000"
    run_dir.mkdir(parents=True, exist_ok=True)
    events = [*EVENTS,
              {"type": "finish", "turns": 2, "usage_total": {"in": 18, "out": 2},
               "payload": {"status": "ok", "summary": "Answering your deploy-target question.",
                           "reply_to": "your question about the deploy target"}}]
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 2})
    ui_page.reload()

    reply = ui_page.locator(".msg.assistant", has_text="Answering your deploy-target question")
    expect(reply).to_be_visible(timeout=10_000)
    # the ↩ reference chip shows WHICH earlier message this reply addresses
    expect(reply.locator(".reply-ref")).to_contain_text("your question about the deploy target")
