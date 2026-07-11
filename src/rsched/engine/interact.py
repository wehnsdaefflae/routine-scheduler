"""Action handlers that converse with the user: ask_user (blocking/deferred questions)
and write_util, whose approval gate derives from the active fragments' grants.

Blocking flows poll the routine inbox for the answer and credit the waiting time back to
the wall-clock budget; unanswered blocking questions degrade to deferred so a run never
hangs forever on a silent user.
"""

from __future__ import annotations

import time

from .. import utils_lib
from ..ids import question_id
from . import inbox
from .control import RunAborted

_APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "okay", "go", "accept", "confirm")


def _is_approval(text: str) -> bool:
    return text.strip().lower().split()[0] in _APPROVE_WORDS if text.strip() else False


def handle_ask(loop, action: dict, poll_s: float) -> dict:
    ctx = loop.ctx
    qid = question_id(ctx.run_ts, ctx.turn)
    mode = action.get("mode") or "deferred"
    if ctx.depth > 0:
        mode = "deferred"  # subruns cannot block the run on the user
    options = list(action.get("options") or [])
    question = action["question"]
    ctx.transcript.event("question", {"qid": qid, "mode": mode, "question": question,
                                      "options": options})
    if mode == "deferred":
        inbox.file_deferred_question(ctx.routine.dir, qid, question, options, ctx.run_ts)
        return {"kind": "ask_user", "qid": qid, "mode": mode}
    ctx.write_status("waiting_user",
                     question={"qid": qid, "question": question, "options": options,
                               "asked": ctx.run_ts})
    deadline = time.monotonic() + ctx.budgets.ask_timeout_h * 3600
    started = time.monotonic()
    answer = None
    while time.monotonic() < deadline:
        if loop._aborted():
            raise RunAborted()
        answer = inbox.take_answer(ctx.routine.dir, qid, loop.consumed_dir)
        if answer:
            break
        time.sleep(poll_s)
    ctx.credit_suspended(time.monotonic() - started)
    ctx.write_status("running", question=None)
    if answer:
        ctx.transcript.event("answer", {"qid": qid, "text": answer["text"],
                                        "source": answer.get("source", "web"),
                                        "intermediate": bool(answer.get("intermediate"))})
        if answer.get("intermediate"):
            # A dialog reply, not the answer: the user needs some back-and-forth before they
            # can decide. The observation tells the model to respond and re-ask — each round
            # is one ordinary turn, so the dialog can go on until a real answer arrives.
            return {"kind": "ask_user", "qid": qid, "mode": mode, "dialog": True,
                    "user_message": answer["text"],
                    "note": "This is a dialog reply, NOT the final answer — the user needs "
                            "more back-and-forth first. Address their message, then ask again "
                            "with ask_user (the original question, or a sharper version)."}
        return {"kind": "ask_user", "qid": qid, "mode": mode, "answered": True,
                "answer": answer["text"]}
    inbox.file_deferred_question(ctx.routine.dir, qid, question, options, ctx.run_ts)
    return {"kind": "ask_user", "qid": qid, "mode": mode, "timed_out": True,
            "timeout_h": ctx.budgets.ask_timeout_h}


def handle_write_util(loop, action: dict, poll_s: float) -> dict:
    ctx = loop.ctx
    name, content = action["name"], action["content"]
    if ctx.depth > 0:
        return {"kind": "write_util", "name": name, "declined": True,
                "reason": "sub-workflows cannot create/revise utils — use existing ones"}
    home = ctx.server.utils_home
    utils_lib.ensure_library(home, remote=ctx.server.libraries_remote)
    creating = not utils_lib.exists(home, name)
    # Approval policy comes from the active fragments' grants (util-authoring: every change;
    # util-authoring-autonomous: creations only). No grants on the ctx = confirm everything.
    if ctx.grants is None or ctx.grants.needs_confirm(creating):
        verb = "create" if creating else "revise"
        ask = handle_ask(loop, {
            "question": f"Approve {verb} of global util '{name}'? First lines:\n"
                        f"{content.strip()[:400]}",
            "mode": "blocking", "options": ["approve", "decline"]}, poll_s)
        if not ask.get("answered"):
            return {"kind": "write_util", "name": name, "pending_approval": True,
                    "qid": ask.get("qid")}
        if not _is_approval(ask["answer"]):
            return {"kind": "write_util", "name": name, "declined": True}
    utils_lib.write_util_file(home, name, content)
    ok, output = utils_lib.selftest(home, name)
    if not ok:
        return {"kind": "write_util", "name": name, "created": creating,
                "selftest_ok": False, "output": output[:2000]}
    utils_lib.git_commit(home, f"{'create' if creating else 'revise'} {name}")
    return {"kind": "write_util", "name": name, "created": creating, "selftest_ok": True}
