"""The console's "ask back" on ANY blocking decision reaches the run at once.

The run view's question panel offers "ask back" on every blocking decision — a plain question,
a util, rule or reminder approval, an access request, the secret-exposure request — and its
toast promises "sent — the model will reply and re-ask". Only the plain question kept that
promise. Every other consumer turned the dialog reply into its own "approval pending"
observation, which named the record and dropped the operator's words, so the run could not
answer the question it had just been asked. And the reply left the ONE `loop.dialog_qid` slot
pointing at the APPROVAL's record, so the next unrelated `ask_user` resolved it.

The owner's decision (2026-10-01): an ask-back ends the wait and reaches the run as the
observation of the action that asked — the words verbatim, and what to do: answer them, then
re-submit the same action. The re-submission SUPERSEDES the open record, because it is the
next decision filed for the same SUBJECT; nothing else can resolve it.

The console's answers are written BEFORE the run starts. A decision's id is
`q-<run-ts>-<turn>`, so the file for the ask on turn N waits in the inbox (the boot drain leaves
an answer with no pending record alone) and the blocking wait takes it on its first poll — no
driver thread racing the ask window.
"""

from __future__ import annotations

import json

import pytest

from conftest import WORKFLOW_MD, finish, util, write_file
from helpers import server_for, set_capabilities
from rsched import reminders as store
from rsched import utils_lib, utils_run
from rsched.engine.interact import _held_not_settled
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json
from rsched.reminders import Reminder

TS = "20260708-070000"
WORDS = "why do you need this — what breaks without it?"
UTIL = '"""frob — test util.\n\nusage: gu frob\ntags: test, demo\nnet: none\nfs: roots\n"""\n'
RULE = """---
effect:
  with: names the evidence behind every claim it makes
  without: may state a claim with nothing behind it
  when: before any claim in a summary
tags: [evidence, claims, summaries]
---
# rule: show-your-work — every claim carries its evidence

- **Name the evidence.** A claim without it is a guess.
"""
REMIND = {"op": "add", "scope": "global", "regex": "^util:fs-ops mv ", "reach": "universal",
          "description": "mv over an existing destination overwrites it silently"}

#: Each blocking decision a run can be parked on → the record type it files, and the
#: instruction its ask-back observation must give for re-submitting the action that asked.
CASES = {
    "util-approval": ("util-approval", "re-submit write_util 'frob'"),
    "rule-approval": ("rule-approval", "re-submit write_rule 'show-your-work'"),
    "reminder-approval": ("reminder-approval", "carry the same `remind` op again"),
    "access-request": ("request", "the same request (reminders:global)"),
    "secret-exposure": ("request", "call util 'frob' again"),
}


def _arm(case: str, d, monkeypatch) -> tuple[dict, list]:
    """Set routine `d` up so `case`'s action files a BLOCKING decision. Returns the action and
    the list a util call is recorded in (the secret gate's util must never run on an ask-back).
    """
    ran: list[str] = []
    if case == "util-approval":
        set_capabilities(d, actions=["write_util"], confirm="always")
        return {"say": "a new util", "kind": "write_util", "name": "frob", "content": UTIL}, ran
    if case == "rule-approval":
        set_capabilities(d, actions=["write_rule"], rule_confirm="always")
        return ({"say": "a new rule", "kind": "write_rule", "name": "show-your-work",
                 "content": RULE}, ran)
    if case == "reminder-approval":
        set_capabilities(d, reminders="global", remind_confirm="always")
        return {**write_file("state/a.txt"), "remind": REMIND}, ran
    if case == "access-request":
        return ({"say": "the shared store would carry this", "kind": "ask_user",
                 "mode": "blocking", "question": "May I keep this caution in the shared store?",
                 "request": "reminders:global"}, ran)
    from rsched import secrets as secrets_mod

    monkeypatch.setattr(utils_run, "run_util", lambda _home, name, _args, **_kw:
                        (ran.append(name) or (0, "ran", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda _home, _name: True)
    monkeypatch.setattr(utils_run, "util_needs", lambda _home, _name:
                        utils_run.UtilNeeds({"FOO_KEY"}, False, set(), True, ()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x"})
    return util("frob"), ran


def _reply(d, turn: int, text: str = WORDS, *, ask_back: bool = True) -> None:
    """The console's answer to the decision the action on `turn` files — the run view's "ask
    back" unless told otherwise (`intermediate` is exactly what the web layer writes for it).
    """
    qid = f"q-{TS}-{turn}"
    atomic_write_json(d / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": text, "source": "web", "intermediate": ask_back})


def _pending(d) -> dict[str, dict]:
    return {r["qid"]: r for p in sorted((d / "questions" / "pending").glob("*.json"))
            if isinstance(r := read_json(p), dict)}


def _observation(ep, call: int) -> str:
    """What the model read on completion `call` about the action before it — the last message."""
    return str(ep.calls[call]["messages"][-1]["content"])


@pytest.mark.parametrize("case", CASES)
def test_an_ask_back_reaches_the_run_as_the_observation_of_the_action_that_asked(
        case, make_routine, scripted, monkeypatch):
    """The words verbatim, the instruction to answer them and re-submit, and the record still
    open — and nothing written, granted or run on the strength of a reply that decided nothing.
    """
    d = make_routine(slug="askback")
    action, ran = _arm(case, d, monkeypatch)
    _reply(d, 1)
    ep = scripted([action, finish(status="partial", summary="answered; the decision is open")])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"

    shown = _observation(ep, 1)
    qtype, resubmit = CASES[case]
    assert WORDS in shown, shown
    assert "the user replied WITHOUT deciding" in shown
    assert resubmit in shown, shown
    assert "the re-submission replaces the open record" in shown

    # the decision stays open — deferred now, the run is no longer parked on it
    rec = _pending(d)[f"q-{TS}-1"]
    assert rec["type"] == qtype and rec["mode"] == "deferred"
    server = server_for(d)
    assert not (server.rules_home / "show-your-work.md").exists()
    assert not list(server.reminders_home.glob("*.json"))
    assert ran == []
    if case == "util-approval":
        assert not utils_lib.exists(server.libraries_home, "frob")
    answers = [e["payload"] for e in read_events(run_dir / "transcript.jsonl")[0]
               if e["type"] == "answer"]
    assert [a["text"] for a in answers] == [WORDS] and answers[0]["intermediate"] is True
    assert "held" not in answers[0]          # delivered at once, never queued as a message


@pytest.mark.parametrize("case", CASES)
def test_the_re_submission_supersedes_the_open_record(case, make_routine, scripted,
                                                      monkeypatch):
    """The operator asks back on the first filing AND on the re-submission, so both records
    would stay open if nothing superseded: exactly the re-submission's must be left."""
    d = make_routine(slug="resubmit")
    action, _ran = _arm(case, d, monkeypatch)
    _reply(d, 1)
    _reply(d, 2, "and what does it cost?")
    scripted([action, {**action, "say": "answered them — re-submitting"},
              finish(status="partial", summary="still open")])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"
    pending = _pending(d)
    assert list(pending) == [f"q-{TS}-2"], "the superseded record is still open"
    assert pending[f"q-{TS}-2"]["type"] == CASES[case][0]


@pytest.mark.parametrize("case", CASES)
def test_a_re_submission_after_a_resume_supersedes_the_record_asked_back_on_before_it(
        case, make_routine, scripted, monkeypatch):
    """The open records are keyed in memory, so a leg that resumed after the ask-back — a
    restart between it and the re-submission, a conversation whose next reply re-submits —
    filed a SECOND record beside the open one: two cards on the Decisions page for one
    decision. The resumed leg re-keys them from the transcript (`askback.rebuild_dialogs`)."""
    d = make_routine(slug="resumed")
    action, _ran = _arm(case, d, monkeypatch)
    _reply(d, 1)
    scripted([action, finish(status="partial", summary="asked back; the decision is open")])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial" and list(_pending(d)) == [f"q-{TS}-1"]
    _reply(d, 3, "and what does it cost?")      # the resumed leg's re-submission is turn 3
    scripted([{**action, "say": "answered them — re-submitting"},
              finish(status="partial", summary="still open")])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS, resume_from=TS)
    assert status == "partial"
    assert list(_pending(d)) == [f"q-{TS}-3"], "the record asked back on before the resume"


@pytest.mark.parametrize("case", CASES)
def test_an_unrelated_ask_leaves_the_open_record_alone(case, make_routine, scripted,
                                                       monkeypatch):
    """The single dialog slot pointed at whatever record was asked back on last, and the next
    plain `ask_user` resolved it — an approval the operator had not decided, deleted."""
    d = make_routine(slug="unrelated")
    action, _ran = _arm(case, d, monkeypatch)
    _reply(d, 1)
    scripted([action,
              {"say": "something else entirely", "kind": "ask_user", "mode": "deferred",
               "question": "Which venue do you prefer?"},
              finish(status="partial", summary="two decisions open")])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"
    pending = _pending(d)
    assert set(pending) == {f"q-{TS}-1", f"q-{TS}-2"}
    assert pending[f"q-{TS}-1"]["type"] == CASES[case][0]


def test_a_request_for_other_entities_is_not_a_re_submission(make_routine, scripted):
    """A request's subject is its entity ids: asking for something else is a new decision."""
    d = make_routine(slug="otherreq")
    _reply(d, 1)
    scripted([{"say": "the shared store", "kind": "ask_user", "mode": "blocking",
               "question": "May I keep this caution in the shared store?",
               "request": "reminders:global"},
              {"say": "and the run history", "kind": "ask_user", "mode": "deferred",
               "question": "May I read every earlier run?", "request": "runs:all"},
              finish(status="partial", summary="two requests open")])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"
    assert {q: r["request"] for q, r in _pending(d).items()} == {
        f"q-{TS}-1": ["reminders:global"], f"q-{TS}-2": ["runs:all"]}


def test_a_re_submission_on_a_finish_goes_through(make_routine, scripted):
    """A `remind` op may ride a finish, and every rung of the finish gate hands the SAME finish
    back — which the replay guard then skipped whole, so the op the run was told to carry
    again was never applied: no new approval, and the asked-back record open for good. An op
    that ended in an ask-back changed nothing, so its re-submission is not a replay — while
    the label riding beside it WAS recorded, and must not count twice.
    """
    recipe = WORKFLOW_MD + "\n## Done when\n\n- d1 — the curated caution is filed\n"
    d = make_routine(slug="finishop", workflow_md=recipe)
    set_capabilities(d, reminders="global", remind_confirm="always")
    store.save_local(d, [Reminder(id="rem-d", regex="^util:danger", scope="local",
                                  description="it wipes the workdir", created_run="r:1",
                                  stats=store.blank_stats())], {})
    _reply(d, 2)                                     # the finish on turn 2 carries the op
    _reply(d, 3, "approve", ask_back=False)
    first = {**finish(status="partial", summary="caution proposed"), "remind": REMIND,
             "remind_feedback": {"id": "rem-d", "label": "didnt"}}
    ep = scripted([util("danger"),                   # HELD — the fire the label answers
                   first,       # no accounting yet — the gate sets it aside, the note rides along
                   {**first, "accounting": ["d1 unmet: the operator approves the caution"]}])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"
    assert WORDS in _observation(ep, 2)
    filed = [json.loads(p.read_text(encoding="utf-8"))
             for p in server_for(d).reminders_home.glob("*.json")]
    assert [r["regex"] for r in filed] == [REMIND["regex"]]       # the re-submission landed
    assert not _pending(d)              # the asked-back record superseded, the new one settled
    tally = store.load_local(d)[0][0].stats
    assert tally["fires"] == 1 and tally["didnt"] == 1             # the label counted once


def test_a_finish_whose_approval_is_asked_back_is_set_aside_for_the_answer(make_routine,
                                                                            scripted):
    """A `remind` op may ride a finish, and the operator's ask-back on the approval it files
    rides a note — but a finish that STANDS carries no observation, so the run ended with their
    question unanswered and the approval open. It is set aside like a user message that
    arrives while finishing; the finish that carries the op again re-submits the approval."""
    d = make_routine(slug="finishask")
    set_capabilities(d, reminders="global", remind_confirm="always")
    _reply(d, 1)                                     # ask back on the first finish's approval
    _reply(d, 2, "approve", ask_back=False)          # …and approve the re-submission
    first = {**finish(status="partial", summary="caution proposed"), "remind": REMIND}
    ep = scripted([first, {**first, "say": "answered them — carrying it again"}])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "partial"
    shown = _observation(ep, 1)
    assert "finish deferred" in shown and WORDS in shown, shown
    deferred = [e["payload"] for e in read_events(run_dir / "transcript.jsonl")[0]
                if e["type"] == "observation" and e["payload"].get("asked_back")]
    assert len(deferred) == 1
    filed = [json.loads(p.read_text(encoding="utf-8"))
             for p in server_for(d).reminders_home.glob("*.json")]
    assert [r["regex"] for r in filed] == [REMIND["regex"]]
    assert not _pending(d)              # the asked-back record superseded, the new one settled


@pytest.mark.parametrize(("qtype", "answer", "held"), [
    ("util-approval", {"text": "Bin hier"}, True),           # names neither option (D38)
    ("rule-approval", {"text": "hmm, maybe"}, True),
    ("reminder-approval", {"text": "later"}, True),
    ("request", {"text": "sure, go ahead"}, True),           # free text is not a decision
    ("util-approval", {"text": "approve"}, False),
    ("request", {"text": "allowed", "decision": "allow_now"}, False),
    ("util-approval", {"text": "Bin hier", "intermediate": True}, False),   # ask back
    ("request", {"text": "why?", "intermediate": True}, False),
    ("question", {"text": "anything at all"}, False),
])
def test_only_a_plain_reply_that_settles_nothing_is_held(qtype, answer, held):
    """A plain reply that is not a decision keeps D38's behaviour — held as a delayed message
    while the run keeps waiting. An ask-back is never held: it ends the wait."""
    assert _held_not_settled(qtype, answer) is held
