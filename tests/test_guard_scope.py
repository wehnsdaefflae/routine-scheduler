"""Once-only guards fire once per GUARD SCOPE — the whole run for a routine, the current reply
for a conversation — and a resumed leg rebuilds them from the transcript (engine/guardscope.py).

Every one of them used to be a fresh set at loop construction, so each resumed leg — after a
restart, an operator resume, a parked question answered after the process died — started them
empty: a hold the run had already confirmed stopped the same action again, a rule assist fired
a second time, the pre-finish deferral recurred and the verifier challenged a line it had
already challenged. The owner's decision: "Routines: rebuilt from the transcript on resume, so
once really means once per run. Conversations: each new reply starts fresh, since each reply is
a new task."

A leg here is interrupted the way a crash or a restart leaves one: the endpoint dies mid-run,
the finish is the engine's and unauthored, and the next leg resumes the same run dir.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from conftest import finish, util, write_file
from helpers import server_for, set_capabilities
from rsched import assists as lib
from rsched import reminders as rem_store
from rsched.endpoints.base import EndpointError
from rsched.engine import finishline, guardscope
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.reminders import Reminder
from test_assists import TS, _age_ledger, _hold_rule, _rule, _write_root


def _down() -> EndpointError:
    """The leg ends here — the shape a crash or a restart leaves: an unauthored finish."""
    return EndpointError("provider down")


def _events(run_dir) -> list[dict]:
    return read_events(run_dir / "transcript.jsonl")[0]


def _observed(run_dir) -> list[str]:
    return [e["payload"].get("kind") for e in _events(run_dir) if e["type"] == "observation"]


def _reminder(d, regex: str) -> None:
    set_capabilities(d, reminders="local")
    rem_store.save_local(d, [Reminder(id="rem-1", regex=regex, description="it clobbers x",
                                      scope="local", created_run="r:1",
                                      stats=rem_store.blank_stats())], {})


# --- a routine: once per RUN, across every leg of it -------------------------------------------

def test_a_hold_confirmed_before_a_resume_is_not_held_again(make_routine, scripted):
    """Re-emitting a held action IS the confirmation to proceed — and stays so after a restart.
    The label the hold asked for still lands on the far side: the hold is the run's, so the
    label it is owed is the run's too."""
    d = make_routine(slug="scope")
    server = server_for(d)
    _reminder(d, r"^write_file path=state/x\.txt")
    scripted([write_file("state/x.txt"), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"

    labelled = {**write_file("state/y.txt"),
                "remind_feedback": {"id": "rem-1", "label": "didnt"}}
    scripted([write_file("state/x.txt"), labelled, finish()])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    assert _observed(run_dir) == ["reminder_hold", "write_file", "write_file"]
    stats = rem_store.load_local(d)[0][0].stats
    assert stats["fires"] == 1 and stats["didnt"] == 1


def test_an_observation_assist_does_not_fire_again_after_a_resume(make_routine, scripted):
    """The second failure of the same call fired fix-the-cause's line; the leg died; the
    resumed leg fails the same way twice more — the line was said, once, for this run."""
    d = make_routine(slug="scope")
    server = server_for(d)
    _rule(server, "fix-the-cause", "observation", "repeated-failure", "change route now")
    _hold_rule(d, ["fix-the-cause"])
    scripted([util("nonexistent-util"), util("nonexistent-util"), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"
    second = [e for e in _events(d / "runs" / TS) if e["type"] == "observation"][1]
    assert second["payload"]["assists"] == ["fix-the-cause/m"]     # the record names the fire

    scripted([util("nonexistent-util"), util("nonexistent-util"), write_file("state/a.txt"),
              finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    assert json.loads(lib.state_path(d).read_text())["fix-the-cause/m"] == 1


def test_a_boundary_assist_does_not_fire_again_after_a_resume(make_routine, scripted):
    """The user corrected the run in flight, the correction line was said; after a restart the
    user corrects it again — the boundary note is not repeated."""
    from rsched.engine.inbox import file_message

    d = make_routine(slug="scope")
    server = server_for(d)
    _rule(server, "fix-the-cause", "boundary", "user-corrected", "name the intention")
    _hold_rule(d, ["fix-the-cause"])

    def correcting(path: str):
        def act():
            file_message(d, "no — use the short form", via="web")   # delivered next boundary
            return write_file(path)
        return act

    scripted([correcting("state/a.txt"), write_file("state/b.txt"), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"
    scripted([correcting("state/c.txt"), write_file("state/d.txt"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    notes = [e["payload"] for e in _events(run_dir) if e["type"] == "user_injection"
             and "[RULE" in e["payload"].get("text", "")]
    assert len(notes) == 1, notes
    assert notes[0]["assists"] == ["fix-the-cause/m"]


def test_the_assist_finish_deferral_does_not_recur_after_a_resume(make_routine, scripted):
    """A run is held at its finish by an assist at most once — a restart between the deferral
    and the next finish does not buy the rule a second negotiation."""
    d = make_routine(slug="scope")
    server = server_for(d)
    _rule(server, "decision-record", "pre-finish", "ledger-untouched", "append one entry")
    _hold_rule(d, ["decision-record"])
    _age_ledger(d)
    scripted([write_file("artifacts/report.md"), finish(), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"

    scripted([finish(), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    deferred = [e["payload"] for e in _events(run_dir) if e["type"] == "observation"
                and e["payload"].get("assist")]
    assert len(deferred) == 1, deferred
    assert deferred[0]["assists"] == ["decision-record/m"]


def test_the_verifier_does_not_challenge_a_line_twice_across_a_resume(make_routine, scripted,
                                                                      monkeypatch):
    """One objection per claimed line, and the model keeps the last word — a resumed leg that
    re-asserts the verdict is not argued with again; the dispute is recorded instead."""
    from rsched.engine import verifier

    d = make_routine(slug="scope")
    server = server_for(d)
    finishline.save(d, {"outcomes": [{"text": "the PDF is verified", "judge": "run"}]},
                    now="t")
    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [
        {"id": "g1", "text": "the PDF is verified", "evidence": "no action opened it"}])
    claim = {**finish(summary="Verified."), "accounting": ["g1 met: I verified it"]}
    scripted([write_file("state/probe.txt"), claim, _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"

    scripted([claim, claim])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    challenged = [e for e in _events(run_dir) if e["type"] == "observation"
                  and e["payload"].get("claims_unsupported")]
    assert len(challenged) == 1
    row = finishline.load(d)["outcomes"][0]
    assert row["status"] == "met" and row["disputed"] == "no action opened it"


def test_a_repo_found_clean_stays_an_undo_point_across_a_resume(make_routine, scripted,
                                                                tmp_path):
    """HEAD restores what the run found — for the whole run. Asked afresh after a restart, the
    run's own first edit read as uncommitted work and its next edit into the same clean repo
    was held: the false positive the clean-tree check exists to remove, one leg later."""
    d = make_routine(slug="scope")
    server = server_for(d)
    _rule(server, "git-checkpoint", "pre-action", "uncheckpointed-repo-write", "commit first")
    _hold_rule(d, ["git-checkpoint"])
    repo = tmp_path / "project"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "README").write_text("r", encoding="utf-8")
    subprocess.run([*git, "add", "README"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    _write_root(d, repo)
    scripted([write_file(str(repo / "a.py"), content="a"), _down()])
    assert run_routine(d, server, run_ts=TS)[0] == "failed"
    first = next(e for e in _events(d / "runs" / TS) if e["type"] == "observation")
    assert Path(first["payload"]["undo_point"]) == repo     # named on the write it let through

    scripted([write_file(str(repo / "b.py"), content="b"),
              write_file(str(repo / "c.py"), content="c"), finish()])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    assert _observed(run_dir) == ["write_file", "write_file", "write_file"]
    assert (repo / "b.py").read_text(encoding="utf-8") == "b"


# --- a conversation: once per REPLY ------------------------------------------------------------

def test_a_new_reply_starts_fresh_while_a_resumed_reply_keeps_its_guards(make_routine,
                                                                         scripted, tmp_path):
    """Each reply is a new task, so the action reply 1 confirmed is held again in reply 2 —
    and reply 2, interrupted and resumed, is still reply 2: its own hold stays confirmed."""
    from rsched.paths import atomic_write_json

    convs = tmp_path / "conversations"
    convs.mkdir()
    made = make_routine(slug="c-scope")
    d = convs / made.name
    made.rename(d)
    server = server_for(d)
    server.conversations_home = convs
    server.routines_home = convs
    _reminder(d, r"^write_file path=state/x\.txt")

    scripted([write_file("state/x.txt"), write_file("state/x.txt"), finish(summary="one")])
    assert run_routine(d, server, run_ts=TS)[0] == "ok"
    assert _observed(d / "runs" / TS) == ["reminder_hold", "write_file"]

    atomic_write_json(d / "inbox" / "msg-1.json", {"text": "and again", "via": "web-converse"})
    scripted([write_file("state/x.txt"), _down()])                 # reply 2: held afresh
    assert run_routine(d, server, run_ts=TS, resume_from=TS)[0] == "failed"
    assert _observed(d / "runs" / TS)[2:] == ["reminder_hold"]

    scripted([write_file("state/x.txt"), write_file("state/x.txt"), finish(summary="two")])
    status, run_dir = run_routine(d, server, run_ts=TS, resume_from=TS)   # reply 2, resumed
    assert status == "ok"
    assert _observed(run_dir)[3:] == ["write_file", "write_file"]


# --- the scope, and what each ledger is rebuilt from -------------------------------------------

def _ctx(tmp_path, *, conversation: bool) -> SimpleNamespace:
    convs = tmp_path / "conversations"
    home = convs if conversation else tmp_path / "routines"
    return SimpleNamespace(routine=SimpleNamespace(dir=home / "x"),
                           server=SimpleNamespace(conversations_home=convs))


def _fin(*, authored: bool) -> dict:
    return {"type": "finish", "payload": {"status": "ok", "authored": authored}}


def _act(turn: int, action: dict) -> dict:
    return {"type": "assistant_action", "turn": turn, "payload": action}


def _obs(turn: int, **payload) -> dict:
    return {"type": "observation", "turn": turn, "payload": payload}


def test_the_scope_is_the_run_for_a_routine_and_the_reply_for_a_conversation(tmp_path):
    reply1 = [_act(1, util("u")), _obs(1, kind="util", exit=0), _fin(authored=True)]
    reply2 = [{"type": "user_injection", "payload": {"text": "and now b"}},
              _act(2, util("u")), _obs(2, kind="util", exit=0)]
    interrupted = [*reply1, *reply2, _fin(authored=False)]
    routine = _ctx(tmp_path, conversation=False)
    conversation = _ctx(tmp_path, conversation=True)
    # a routine: every leg is the same run, a finish authored or not
    assert guardscope.in_scope(routine, reply1) == reply1
    assert guardscope.in_scope(routine, interrupted) == interrupted
    # a conversation: the leg after a reply was handed back opens the next one…
    assert guardscope.in_scope(conversation, reply1) == []
    # …the leg after an engine-written finish continues the reply it interrupted…
    assert guardscope.in_scope(conversation, interrupted) == [*reply2, _fin(authored=False)]
    # …the first reply has nothing before it…
    first = [_act(1, util("u")), _fin(authored=False)]
    assert guardscope.in_scope(conversation, first) == first
    # …and slash commands run while the turn was the user's hand nothing over
    commands = [*reply1, {"type": "user_injection", "payload": {"text": "/x", "command": True}},
                {"type": "observation", "payload": {"kind": "util", "user_command": True}}]
    assert guardscope.in_scope(conversation, commands) == []


def test_each_ledger_is_rebuilt_from_what_its_events_record(tmp_path):
    events = [
        _act(1, write_file("state/x.txt")),
        _obs(1, kind="reminder_hold", action="write_file path=state/x.txt",
             reminders=[{"id": "rem-1"}]),
        _act(2, write_file("/p/repo/a.py")),
        _obs(2, kind="assist_hold", action="write_file path=/p/repo/a.py",
             assists=["git-checkpoint/m"]),
        _act(3, write_file("/q/repo/b.py")),
        _obs(3, kind="write_file", path="/q/repo/b.py", bytes=1, undo_point="/q/repo",
             assists=["ask-policy/m"]),
        {"type": "user_injection", "payload": {"text": "[RULE …]", "source": "engine",
                                               "assists": ["fix-the-cause/m"]}},
        {"type": "user_injection", "payload": {"text": "… ARCHIVED …", "source": "engine",
                                               "evict_warning": True}},
        _act(4, finish()),
        _obs(4, kind="finish", rejected=True, assist=True, assists=["decision-record/m"],
             message="m"),
        _act(5, finish()),
        _obs(5, kind="finish", rejected=True, claims_unsupported=["d1", "g2"], message="m"),
    ]
    loop = SimpleNamespace(ctx=_ctx(tmp_path, conversation=False))
    guardscope.rebuild(loop, events)
    assert loop.holds == {("reminder", "write_file path=state/x.txt"),
                          ("rule", "write_file path=/p/repo/a.py")}
    assert loop.assists_fired == {"git-checkpoint/m", "ask-policy/m", "fix-the-cause/m",
                                  "decision-record/m"}
    assert loop.assist_finish_deferred is True
    assert loop.assist_undo_points == {Path("/q/repo")}
    assert loop._challenged == {"d1", "g2"}
    assert loop._evict_warned is True
    assert loop.reminder_owed == {"rem-1": 1}
    # the next reply of a conversation reads none of it
    loop = SimpleNamespace(ctx=_ctx(tmp_path, conversation=True))
    guardscope.rebuild(loop, [*events, _fin(authored=True)])
    assert (loop.holds, loop.assists_fired, loop._challenged, loop.reminder_owed) == (
        set(), set(), set(), {})
    assert loop.assist_finish_deferred is False and loop._evict_warned is False


def test_the_label_ledger_replays_in_the_order_the_live_turn_applies_it():
    """Nothing extra is recorded for a label — it rides its action — so the rebuild has to
    settle each one exactly as `remind._apply_feedback` did: against a hold still owed, after
    the hold of that very action, once per finish payload, and never for an action the
    reserved finish turn refused to run."""
    from rsched.engine import remind_ledger
    from rsched.engine.loop import RESERVED_REFUSAL

    def act(turn, rid=""):
        fb = {"remind_feedback": {"id": rid, "label": "did"}} if rid else {}
        return _act(turn, {**write_file("state/x.txt"), **fb})

    def held(turn, *ids):
        return _obs(turn, kind="reminder_hold", action="write_file path=state/x.txt",
                    reminders=[{"id": i} for i in ids])

    events = [
        act(1, "rem-0"), _obs(1, kind="write_file", bytes=1),   # no hold behind it: nothing
        act(2), held(2, "rem-1", "rem-2"),                       # one hold, two owed
        act(3, "rem-1"), _obs(3, kind="write_file", bytes=1),   # rem-1 labelled
        act(4, "rem-3"), held(4, "rem-3"),                       # held, then labelled
        act(5, "rem-2"), _obs(5, kind="write_file", rejected=True, reason=RESERVED_REFUSAL),
    ]
    loop = SimpleNamespace()
    remind_ledger.rebuild(loop, events)
    assert loop.reminder_owed == {"rem-2": 1}
    labelled = {**finish(), "remind_feedback": {"id": "rem-2", "label": "did"}}
    events += [_act(6, labelled), _obs(6, kind="finish", rejected=True, message="m"),
               _act(7, labelled)]                                # the gate handed it back
    remind_ledger.rebuild(loop, events)
    assert loop.reminder_owed == {} and len(loop.reminder_replayed) == 1


def test_the_hold_table_names_each_layers_own_source():
    """`is_hold` and the ledger's rebuild read one table, so a kind cannot be recognised by
    one and keyed differently by the other — and the key is the layer's own."""
    from rsched.engine import assist, hold, remind

    assert hold.HOLD_SOURCES == {"reminder_hold": remind.SOURCE, "assist_hold": assist.SOURCE}


def test_the_eviction_warning_is_given_once_across_a_resume(make_routine):
    """The resumed leg's window is replayed in full and evicted again — the warning that the
    middle is about to go was given, once, and the resumed boot knows it."""
    from rsched.engine.boot import boot
    from rsched.engine.loop import EngineLoop
    from rsched.engine.window import compact_if_needed, note_prompt_size
    from test_token_calibration import REF, _loop_with_prompt

    first = _loop_with_prompt(make_routine)
    first._schema_off = True
    note_prompt_size(first, REF, {"in": 100_000, "cached_in": 50_000, "cache_write": 10_000})
    compact_if_needed(first, None, REF)
    assert first._evict_warned is True
    notes = [e["payload"] for e in _events(first.ctx.run_dir)
             if e["type"] == "user_injection" and e["payload"].get("evict_warning")]
    assert len(notes) == 1 and "about to be ARCHIVED" in notes[0]["text"]

    resumed = EngineLoop(first.ctx, "## Run flow", "instr", resume=True)
    assert resumed._evict_warned is False
    boot(resumed)
    assert resumed._evict_warned is True
