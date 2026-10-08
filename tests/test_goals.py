"""The run's GOALS (engine/goals.py, engine/goalops.py) — one mechanism for routines and
conversations: what a PERSON asked of a run, transcribed by the run as their scribe and checked
against their own words, answered at the finish — and in a conversation, met by a reply that
declares itself final.

The verifier is stubbed wherever a `met` is claimed: a live judge would consume a scripted reply
for a verdict these tests say nothing about (the claim check has its own tests in test_loop).
"""

from __future__ import annotations

import json

import pytest

from conftest import finish, write_file
from helpers import server_for, transcript_events
from rsched.engine import goalops, goals, verifier
from rsched.engine.actions import normalize_action
from rsched.engine.runtime import run_routine

TS = "20261008-120000"


def goal(verb: str, **fields) -> dict:
    return {"say": "Keeping the goals.", "kind": "goal", "verb": verb, **fields}


def reply(summary: str = "done", *, final: bool | None = None,
          accounting: list[str] | None = None) -> dict:
    return {**finish(summary=summary), **({"final": final} if final is not None else {}),
            **({"accounting": accounting} if accounting else {})}


def _conversation(make_routine, tmp_path, first: str):
    convs = tmp_path / "conversations"
    convs.mkdir(exist_ok=True)
    made = make_routine(slug="c-goals", instruction=first)
    d = convs / made.name
    made.rename(d)
    server = server_for(d)
    server.conversations_home = convs
    server.routines_home = convs
    return d, server


def _observations(run_dir, kind: str) -> list[dict]:
    return [e["payload"] for e in transcript_events(run_dir)
            if e["type"] == "observation" and e["payload"].get("kind") == kind]


def _status(run_dir) -> dict:
    return json.loads((run_dir / "status.json").read_text(encoding="utf-8"))


@pytest.fixture
def judge_agrees(monkeypatch):
    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [])


# ---- the person's words ------------------------------------------------------------------------

def test_a_quote_is_found_in_one_text_whatever_its_quote_marks_and_case():
    texts = ["Please fix the flaky LOGIN test — and add a “changelog” entry.", "later words"]
    assert goals.heard_at("fix the flaky login test", texts) == 0
    assert goals.heard_at('"add a \'changelog\' entry."', texts) == 0
    assert goals.heard_at("fix the flaky … changelog", texts) == 0     # two excerpts, one text
    assert goals.heard_at("fix the flaky … later words", texts) is None  # never across two
    assert goals.heard_at("deploy to production", texts) is None
    assert goals.heard_at("…", texts) is None                            # nothing left to find
    assert goals.heard_at("later words", texts, after=0) == 1
    assert goals.heard_at("fix the flaky login test", texts, after=0) is None


def test_only_the_person_counts_never_a_machine_channel_or_the_engine():
    events = [
        {"type": "user_injection", "payload": {"text": "the operator's words", "via": "web"}},
        {"type": "user_injection", "payload": {"text": "a sibling's report", "via": "report",
                                               "report": True}},
        {"type": "user_injection", "payload": {"text": "a branch hand-back", "via": "branch"}},
        {"type": "user_injection", "payload": {"text": "an engine note", "source": "engine"}},
        {"type": "user_injection", "payload": {"text": "/util ls", "command": True}},
        {"type": "answer", "payload": {"qid": "q1", "text": "yes, both"}},
    ]
    assert goals.person_texts(events, opening="first message", brief="the brief") == [
        "the brief", "first message", "the operator's words", "yes, both"]


# ---- the ledger --------------------------------------------------------------------------------

def test_the_brief_seeds_b1_and_the_transcript_has_the_last_word():
    assert goals.seed("") == []
    (b1,) = goals.seed("re-check Q3")
    assert (b1["id"], b1["source"], b1["status"], b1["text"]) == (
        "b1", "brief", "open", "re-check Q3")
    later = [{**goals.seed("re-check Q3")[0], "status": "met"}]
    events = [{"type": "observation", "payload": {"kind": "goal", "ledger": []}},
              {"type": "stopping_update", "payload": {"goals": later}}]
    assert goals.replay(events, "re-check Q3") == later
    assert goals.replay([], "re-check Q3") == goals.seed("re-check Q3")


def test_a_dropped_id_is_never_reused():
    ledger = [{**goals.blank("b1", "a", "a", source="person", heard=0, turn=1),
               "status": "dropped"},
              goals.blank("b2", "b", "b", source="person", heard=0, turn=1)]
    assert goals.next_id(ledger) == "b3"
    assert [g["id"] for g in goals.owed(ledger)] == ["b2"]


def test_the_grammar_is_checked_before_the_turn_is_spent():
    assert goalops.field_problems(goal("add", goals=[{"text": "t", "quote": "q"}])) == []
    assert goalops.field_problems(goal("open"))[0].startswith("kind=goal requires 'verb'")
    assert "needs a non-empty 'text'" in goalops.field_problems(goal("add", goals=[{"text": "t"}]))[0]
    assert "exactly ONE entry" in goalops.field_problems(
        goal("change", id="b1", goals=[{"text": "a", "quote": "a"}, {"text": "b", "quote": "b"}]))[0]
    assert "requires 'quote'" in goalops.field_problems(goal("drop", id="b1"))[0]
    assert "requires 'evidence'" in goalops.field_problems(goal("check", id="b1"))[0]
    assert "requires 'id'" in goalops.field_problems(goal("check", evidence="x"))[0]


def test_final_false_is_a_declaration_and_survives_the_padding_rule():
    out = normalize_action({"say": "s", "kind": "finish", "status": "ok", "summary": "x",
                            "final": False, "cancel": False})
    assert out["final"] is False and "cancel" not in out


def test_a_child_run_has_no_goals():
    from rsched.grantpolicy import GrantPolicy

    assert GrantPolicy().allows_kind("goal")
    assert not GrantPolicy(is_subrun=True).allows_kind("goal")


# ---- a conversation ----------------------------------------------------------------------------

@pytest.mark.usefixtures("judge_agrees")
def test_a_final_reply_meets_every_goal_the_first_message_asked_for(make_routine, tmp_path,
                                                                     scripted):
    d, server = _conversation(make_routine, tmp_path,
                              "Please fix the flaky login test and add a changelog entry.")
    scripted([
        goal("set", goals=[
            {"text": "the login test passes ten runs in a row",
             "quote": "fix the flaky login test"},
            {"text": "CHANGELOG.md has an entry for the fix", "quote": "add a changelog entry"}]),
        goal("check", id="b1", evidence="ten green runs in the last observation"),
        reply("Fixed.", final=True),                                   # b2 unanswered
        reply("Fixed.", final=True, accounting=["b2 met: CHANGELOG.md line 3"]),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    deferred = [p for p in _observations(run_dir, "finish") if p.get("rejected")]
    assert [p["accounting"]["missing"] for p in deferred] == [["b2"]]
    fin = next(e for e in transcript_events(run_dir) if e["type"] == "finish")
    assert fin["payload"]["final"] is True
    doc = _status(run_dir)
    assert doc["final"] is True
    assert [(g["id"], g["status"]) for g in doc["goals"]] == [("b1", "met"), ("b2", "met")]
    assert doc["goals"][1]["evidence"] == "CHANGELOG.md line 3"


def test_words_the_person_never_wrote_are_not_a_goal(make_routine, tmp_path, scripted):
    d, server = _conversation(make_routine, tmp_path, "Write the report on Q3.")
    scripted([
        goal("add", goals=[{"text": "it is deployed", "quote": "deploy to production"}]),
        goal("add", goals=[{"text": "the Q3 report is written", "quote": "write the report"}]),
        reply("Started."),                         # an open goal, no declaration → deferred
        reply("Outline drafted; the numbers come next.", final=False),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    refused, added = _observations(run_dir, "goal")
    assert refused["rejected"] and "'deploy to production'" in refused["reason"]
    assert [g["id"] for g in added["ledger"]] == ["b1"]
    assert [p.get("final_undeclared") for p in _observations(run_dir, "finish")] == [True]
    doc = _status(run_dir)
    assert doc["final"] is False and doc["goals"][0]["status"] == "open"


def test_a_final_reply_may_not_leave_a_goal_unmet(make_routine, tmp_path, scripted):
    d, server = _conversation(make_routine, tmp_path, "Write the report on Q3.")
    scripted([
        goal("set", goals=[{"text": "the Q3 report is written", "quote": "write the report"}]),
        reply("Done-ish.", final=True, accounting=["b1 unmet: the ledger export is missing"]),
        reply("The export is missing — I need it from you.", final=False),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    refused = next(p for p in _observations(run_dir, "finish") if p.get("rejected"))
    assert refused["accounting"]["refused"][0].startswith("b1: a FINAL reply meets every open")


def test_a_reply_that_is_not_final_closes_no_goal_it_never_had_checked(make_routine, tmp_path,
                                                                      scripted, monkeypatch):
    """A non-final reply owes nothing, so nothing it claims is put to the judge — and a claim no
    judge read must not close a goal (the stamp takes only the lines the finish owed)."""
    asked = []
    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: asked.append(claims)
                        or [])
    d, server = _conversation(make_routine, tmp_path, "Write the report on Q3.")
    scripted([
        goal("set", goals=[{"text": "the Q3 report is written", "quote": "write the report"}]),
        reply("Mostly there.", final=False, accounting=["b1 met: trust me"]),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok" and asked == []
    assert _status(run_dir)["goals"][0]["status"] == "open"


def test_goals_outlive_the_reply_and_only_later_words_withdraw_one(make_routine, tmp_path,
                                                                   scripted):
    from rsched.paths import atomic_write_json

    d, server = _conversation(make_routine, tmp_path, "Write the report on Q3.")
    scripted([goal("set", goals=[{"text": "the Q3 report is written",
                                  "quote": "write the report"}]),
              reply("Outline drafted.", final=False)])
    assert run_routine(d, server, run_ts=TS)[0] == "ok"

    atomic_write_json(d / "inbox" / "msg-1.json",
                      {"text": "forget the report, just send me the numbers", "via": "web-converse"})
    ep = scripted([
        goal("drop", id="b1", quote="write the report"),       # the words it came from
        goal("drop", id="b1", quote="forget the report"),      # the person's later words
        reply("Numbers: 1, 2, 3.", final=True),                 # nothing open: nothing owed
    ])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    assert "○ [b1] the Q3 report is written" in ep.calls[0]["messages"][0]["content"]
    *_, early, later = _observations(run_dir, "goal")
    assert early["rejected"] and "AFTER the words this goal came from" in early["reason"]
    assert later["ledger"][0]["status"] == "dropped"
    assert later["ledger"][0]["dropped_by"] == "forget the report"


def test_a_check_is_challenged_once_and_then_stands(make_routine, tmp_path, scripted,
                                                    monkeypatch):
    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [
        {"id": c["id"], "text": c["text"], "evidence": "no action ran the tests"} for c in claims])
    d, server = _conversation(make_routine, tmp_path, "Make the tests pass.")
    scripted([
        goal("set", goals=[{"text": "the suite is green", "quote": "make the tests pass"}]),
        goal("check", id="b1", evidence="all green"),                  # challenged: stays open
        goal("check", id="b1", evidence="all green — the run above"),  # stands, disputed
        reply("Green.", final=True),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    _set, first, second = _observations(run_dir, "goal")
    assert first["claims_unsupported"] == ["b1"] and first["ledger"][0]["status"] == "open"
    assert second["ledger"][0]["status"] == "met"
    assert second["ledger"][0]["disputed"] == "no action ran the tests"


# ---- a routine ---------------------------------------------------------------------------------

@pytest.mark.usefixtures("judge_agrees")
def test_a_routine_run_owes_what_the_operator_asked_mid_run_not_its_own_recipe(
        make_routine, scripted):
    from rsched.engine.inbox import file_message

    d = make_routine(slug="goalrun", instruction="publish the weekly digest")
    server = server_for(d)

    def interrupted():
        file_message(d, "also archive last week's digest", via="web")   # delivered next turn
        return write_file("state/a.txt")

    scripted([
        interrupted,
        goal("add", goals=[{"text": "the digest is published",
                            "quote": "publish the weekly digest"}]),   # the recipe's, not theirs
        goal("add", goals=[{"text": "last week's digest is in archive/",
                            "quote": "archive last week's digest"}]),
        finish(),                                                      # owes b1
        {**finish(), "accounting": ["b1 met: moved to archive/2026-40.md"]},
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    recipe, added = _observations(run_dir, "goal")
    assert recipe["rejected"] and added["ledger"][0]["quote"] == "archive last week's digest"
    deferred = [p for p in _observations(run_dir, "finish") if p.get("rejected")]
    assert [p["accounting"]["missing"] for p in deferred] == [["b1"]]
    upd = next(e for e in transcript_events(run_dir) if e["type"] == "stopping_update")
    assert upd["payload"]["goals"][0]["status"] == "met"
