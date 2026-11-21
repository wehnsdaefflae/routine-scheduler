"""F807: a BLOCKING question carrying a `config_patch` must be answerable — and appliable —
where it was asked.
"""

from __future__ import annotations

import threading
import time

from conftest import finish
from helpers import server_for
from rsched.engine.runtime import run_routine
from rsched.paths import atomic_write_json, read_json

TS = "20260708-070000"
PATCH = {"budgets": {"max_turns": 120}}


def _watch_status(run_dir, holder: dict, *, timeout_s: float = 8.0) -> threading.Thread:
    """Catch the LIVE question status.json publishes while the run blocks, then answer it."""
    def watch() -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status = read_json(run_dir / "status.json")
            question = status.get("question") if isinstance(status, dict) else None
            if question and question.get("qid"):
                holder["question"] = question
                atomic_write_json(run_dir.parent.parent / "inbox" / f"answer-{question['qid']}.json",
                                  {"qid": question["qid"], "text": "yes", "source": "web"})
                return
            time.sleep(0.02)

    thread = threading.Thread(target=watch)
    thread.start()
    return thread


def _ask(question: str, **over) -> list[dict]:
    return [{"say": "propose it", "kind": "ask_user", "mode": "blocking", "question": question,
             **over}, finish()]


def test_a_blocking_patch_ask_publishes_the_patch_into_the_live_question(make_routine, scripted):
    """The field the chat card needs. Without it the pinned panel renders a question whose
    proposal it cannot see, and the operator is sent to another page to apply it."""
    d = make_routine(slug="livepatch", budgets={"ask_timeout_min": 1})
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    holder: dict = {}
    watcher = _watch_status(run_dir, holder)
    scripted(_ask("Raise the turn budget to 120?", config_patch=PATCH))
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    watcher.join()
    assert status == "ok"
    live = holder.get("question")
    assert live, "the run never published a live blocking question"
    assert live["config_patch"] == PATCH, live
    # the home the apply route is chosen from: a routine's proposal PATCHes /api/routines
    assert live["routine"] == "livepatch", live
    assert live.get("conversation") is not True, live


def test_a_plain_blocking_ask_publishes_no_patch_fields(make_routine, scripted):
    """No reader learns to expect a field that is absent — the same rule `patchreceipt` keeps:
    an ordinary ask gains nothing, so a card with no proposal grows no apply control."""
    d = make_routine(slug="plainask", budgets={"ask_timeout_min": 1})
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    holder: dict = {}
    watcher = _watch_status(run_dir, holder)
    scripted(_ask("Which folder?"))
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    watcher.join()
    assert status == "ok"
    live = holder.get("question")
    assert live, "the run never published a live blocking question"
    assert "config_patch" not in live, live
    assert "config_target" not in live, live


def test_the_live_decisions_row_carries_the_patch_while_the_run_blocks(make_routine):
    """The same fix reaches the Decisions page's LIVE row, which is not the durable record."""
    from rsched.web.decisions_read import open_decisions

    d = make_routine(slug="liverow")
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    question = {"qid": "q-1", "question": "Raise it?", "options": [], "mode": "blocking",
                "asked": TS, "type": "question", "config_patch": PATCH, "routine": "liverow"}
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"liverow:{TS}", "state": "waiting_user", "turn": 1,
                       "question": question})
    rows = [q for q in open_decisions(server_for(d)) if q["qid"] == "q-1"]
    assert rows, "the live blocking question reached no decision surface"
    assert rows[0]["mode"] == "blocking"
    assert rows[0]["config_patch"] == PATCH, rows[0]


def test_a_patch_for_another_routine_publishes_its_target(make_routine, scripted):
    """D123/F458: a patch may be FOR another routine. The live copy carries the resolved
    target, so the card the operator reads names the routine it would change — and applies it
    there, not to the asker."""
    make_routine(slug="other")
    d = make_routine(slug="asker", budgets={"ask_timeout_min": 1})
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True, exist_ok=True)
    holder: dict = {}
    watcher = _watch_status(run_dir, holder)
    scripted(_ask("Raise other's budget?",
                  config_patch={"routine": "other", "budgets": {"max_turns": 90}}))
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    watcher.join()
    assert status == "ok"
    live = holder.get("question")
    assert live, "the run never published a live blocking question"
    assert live["config_target"] == "other", live
    # the routing key is popped from the body before it is published, exactly as the durable
    # record carries it — what remains IS the PATCH body
    assert live["config_patch"] == {"budgets": {"max_turns": 90}}, live
