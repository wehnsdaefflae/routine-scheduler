"""The unified decision surface: one record shape for every kind of required feedback,
timeout-continues-on-default, and the Discord mirror's synchronization behavior."""

from __future__ import annotations

import json
import threading
import time

from conftest import finish
from rsched import notify
from rsched.config import ServerConfig
from rsched.engine import decisions
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.grants import GrantPolicy
from rsched.paths import atomic_write_json, read_json

TS = "20260708-070000"


def _server(routine_dir) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = routine_dir.parent          # hermetic: .control logs land in tmp
    s.libraries_home = routine_dir.parent.parent / "test-library"
    return s


def _events(run_dir):
    return read_events(run_dir / "transcript.jsonl")[0]


def test_open_questions_flags_answered_when_answer_waiting(make_routine):
    from rsched.engine.inbox import open_questions

    d = make_routine(slug="answered")
    pending = d / "questions" / "pending"
    pending.mkdir(parents=True)
    atomic_write_json(pending / "q1.json", {"qid": "q1", "question": "Ship it?", "options": []})
    atomic_write_json(pending / "q2.json", {"qid": "q2", "question": "Later?", "options": []})
    # No answers waiting -> neither flagged answered.
    qs = {q["qid"]: q for q in open_questions(d)}
    assert qs["q1"].get("answered") is None and qs["q2"].get("answered") is None
    # An answer for q1 lands in the inbox (answered on the Decisions page, not yet drained
    # by a run) -> q1 shows answered-and-queued, q2 stays open.
    atomic_write_json(d / "inbox" / "answer-q1.json", {"qid": "q1", "text": "yes", "source": "web"})
    qs = {q["qid"]: q for q in open_questions(d)}
    assert qs["q1"]["answered"] is True
    assert qs["q2"].get("answered") is None


# ------------------------------------------------------------------ the decision record


def test_blocking_ask_files_a_durable_record_with_default_and_expiry(make_routine, scripted):
    d = make_routine(slug="blocker", budgets={"ask_timeout_min": 0})
    scripted([
        {"say": "q", "kind": "ask_user", "question": "Ship it?", "mode": "blocking",
         "options": ["yes", "no"], "default": "hold the release"},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    # timed out → the record survives as an open DEFERRED decision, default intact
    recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
    assert len(recs) == 1
    rec = recs[0]
    assert rec["mode"] == "deferred" and rec["type"] == "question"
    assert rec["default"] == "hold the release" and rec["options"] == ["yes", "no"]
    # the run CONTINUED on the stated default
    events = _events(run_dir)
    obs = next(e for e in events if e["type"] == "observation" and e["payload"]["kind"] == "ask_user")
    assert obs["payload"]["timed_out"] and obs["payload"]["default"] == "hold the release"
    q = next(e for e in events if e["type"] == "question")
    assert q["payload"]["type"] == "question" and q["payload"]["default"] == "hold the release"


def test_deferred_ask_carries_config_patch_for_the_bridge(make_routine, scripted):
    """The config bridge: a revise run routes a config-shaped request to a deferred ask_user
    carrying a config_patch (a run can't edit routine.yaml itself); it rides the durable
    decision record for the Decisions page's one-click apply."""
    d = make_routine(slug="cbridge")
    patch = {"budgets": {"max_turns": 120}}
    scripted([
        {"say": "propose the config change", "kind": "ask_user", "mode": "deferred",
         "question": "Raise the turn budget to 120?", "config_patch": patch},
        finish(),
    ])
    status, _ = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
    assert len(recs) == 1 and recs[0]["config_patch"] == patch


def test_blocking_answer_resolves_the_record(make_routine, scripted):
    d = make_routine(slug="resolved", budgets={"ask_timeout_min": 1})

    def answer_soon():
        deadline = time.time() + 5
        while time.time() < deadline:
            if list((d / "questions" / "pending").glob("*.json")):
                rec = read_json(next(iter((d / "questions" / "pending").glob("*.json"))))
                assert rec["mode"] == "blocking" and rec["expires"]
                atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                                  {"qid": rec["qid"], "text": "yes", "source": "web"})
                return
            time.sleep(0.02)

    t = threading.Thread(target=answer_soon)
    t.start()
    scripted([
        {"say": "q", "kind": "ask_user", "question": "Go?", "mode": "blocking"},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    t.join()
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json"))   # resolved, not lingering
    ans = next(e for e in _events(run_dir) if e["type"] == "answer")
    assert ans["payload"]["text"] == "yes" and ans["payload"]["source"] == "web"


def test_util_approval_is_the_same_record_with_its_own_type(make_routine, scripted, tmp_path):
    # write_util + confirm "always" ride the routine's DEFAULT capabilities — no permission
    # doc is needed for the approval gate to fire (docs carry conduct prose, not the gate).
    d = make_routine(slug="approval", budgets={"ask_timeout_min": 0})
    scripted([
        {"say": "new util", "kind": "write_util", "name": "frob",
         "content": '"""frob — test util.\n\nusage: gu frob\ntags: test, demo\nnet: none\n"""\n'},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    q = next(e for e in _events(run_dir) if e["type"] == "question")
    assert q["payload"]["type"] == "util-approval"
    assert "NOT applied until approved" in q["payload"]["default"]
    rec = read_json(next(iter((d / "questions" / "pending").glob("*.json"))))
    assert rec["type"] == "util-approval"


def test_dialog_reply_keeps_the_record_open_and_a_reask_supersedes_it(make_routine, scripted):
    """An intermediate ("ask back") reply is NOT the answer: the record survives as deferred
    while the dialog continues, and the model's re-ask supersedes it — so exactly one open
    decision exists at any time and a real answer resolves everything."""
    d = make_routine(slug="dialog", budgets={"ask_timeout_min": 1})
    seen: dict = {"first": None}

    def driver():
        deadline = time.time() + 180  # must outlive the run's whole ask budget (2×ask_timeout_min); 30s flaked under full-suite load
        while time.time() < deadline:
            recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
            blocking = [r for r in recs if r.get("mode") == "blocking"]
            if seen["first"] is None and blocking:
                seen["first"] = blocking[0]["qid"]
                atomic_write_json(d / "inbox" / f"answer-{seen['first']}.json",
                                  {"qid": seen["first"], "text": "which options do I have?",
                                   "source": "web", "intermediate": True})
            elif seen["first"] and blocking and blocking[0]["qid"] != seen["first"]:
                atomic_write_json(d / "inbox" / f"answer-{blocking[0]['qid']}.json",
                                  {"qid": blocking[0]["qid"], "text": "yes", "source": "web"})
                return
            time.sleep(0.02)

    t = threading.Thread(target=driver)
    t.start()
    scripted([
        {"say": "q", "kind": "ask_user", "question": "Go?", "mode": "blocking"},
        {"say": "re-ask with options", "kind": "ask_user", "mode": "blocking",
         "question": "Go? Options: yes (ship now) / no (hold)."},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    t.join()
    assert status == "ok"
    events = _events(run_dir)
    first_obs = next(e for e in events if e["type"] == "observation"
                     and e["payload"]["kind"] == "ask_user")
    assert first_obs["payload"].get("dialog") is True
    answers = [e["payload"] for e in events if e["type"] == "answer"]
    assert answers[0]["intermediate"] is True and answers[1]["text"] == "yes"
    # the superseded record and the answered one are both gone — nothing lingers
    assert not list((d / "questions" / "pending").glob("*.json"))


def test_dialog_reply_survives_a_finish_without_reask(make_routine, scripted):
    """If the model finishes without re-asking after a dialog reply, the decision is NOT
    silently dropped — it stays open as a deferred record for the next run."""
    d = make_routine(slug="dialogdrop", budgets={"ask_timeout_min": 1})

    def driver():
        deadline = time.time() + 180  # must outlive the run's whole ask budget (2×ask_timeout_min); 30s flaked under full-suite load
        while time.time() < deadline:
            recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
            blocking = [r for r in recs if r.get("mode") == "blocking"]
            if blocking:
                atomic_write_json(d / "inbox" / f"answer-{blocking[0]['qid']}.json",
                                  {"qid": blocking[0]["qid"], "text": "hmm, tell me more",
                                   "source": "web", "intermediate": True})
                return
            time.sleep(0.02)

    t = threading.Thread(target=driver)
    t.start()
    scripted([
        {"say": "q", "kind": "ask_user", "question": "Proceed?", "mode": "blocking"},
        finish(status="partial", summary="ended mid-dialog"),
    ])
    status, _run_dir = run_routine(d, _server(d), run_ts=TS)
    t.join()
    assert status == "partial"
    recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
    assert len(recs) == 1 and recs[0]["mode"] == "deferred"   # open for the next run


def test_notify_is_the_single_outbound_seam(monkeypatch):
    """notify.discord_enabled honours both gates — the engine's granted-util set and the
    daemon's held-permissions list — and requires the channel util to exist at all."""
    s = ServerConfig()
    monkeypatch.setattr(notify.utils_lib, "exists", lambda home, name: True)
    assert notify.discord_enabled(s, granted_utils={"discord"})
    assert not notify.discord_enabled(s, granted_utils=set())
    assert notify.discord_enabled(s, permissions=["communication"])
    assert not notify.discord_enabled(s, permissions=["memory"])
    monkeypatch.setattr(notify.utils_lib, "exists", lambda home, name: False)
    assert not notify.discord_enabled(s, granted_utils={"discord"})


# ------------------------------------------------------------------ the Discord mirror


class _FakeDiscord:
    """Records every discord util call; feeds back scripted read replies."""

    def __init__(self, replies=()):
        self.calls: list[list[str]] = []
        self.replies = list(replies)

    def run_util(self, home, name, args, timeout=0, policy=None):
        assert name == "discord"
        self.calls.append(list(args))
        if args[0] == "read":
            batch = self.replies.pop(0) if self.replies else []
            return 0, json.dumps(batch), ""
        return 0, "", ""

    def sends(self):
        return [a for a in self.calls if a[0] == "send"]


def _mirror_ctx(make_routine, slug, *, granted=True):
    from rsched.config import load_routine
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript

    d = make_routine(slug=slug)
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=_server(d), registry=None, run_ts=TS,
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy(active=("communication",),
                             utils=frozenset({"discord"} if granted else set()))
    return ctx


def test_mirror_requires_the_communication_permission(make_routine, monkeypatch):
    fake = _FakeDiscord()
    monkeypatch.setattr(notify.utils_lib, "run_util", fake.run_util)
    monkeypatch.setattr(notify.utils_lib, "exists", lambda home, name: True)
    ctx = _mirror_ctx(make_routine, "nomirror", granted=False)
    assert decisions.mirror_blocking(ctx, "q1", "Go?", [], "", 8) is None
    assert fake.calls == []                       # never touches the channel ungranted


def test_mirror_sends_polls_and_notifies(make_routine, monkeypatch):
    # one empty read (cursor prime), then a reply on the first poll
    fake = _FakeDiscord(replies=[[], [{"text": "yes please"}]])
    monkeypatch.setattr(notify.utils_lib, "run_util", fake.run_util)
    monkeypatch.setattr(notify.utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(decisions, "DISCORD_POLL_S", 0)
    ctx = _mirror_ctx(make_routine, "mirrored")
    mirror = decisions.mirror_blocking(ctx, "q1", "Ship v2 today?", ["yes", "no"],
                                       "wait for Monday", 8)
    assert mirror is not None
    send = fake.sends()[0]
    assert "Ship v2 today?" in send[1] and "wait for Monday" in send[1]
    assert "Decisions page" in send[1]            # cross-surface pointer in the message
    assert mirror.poll() == "yes please"
    mirror.notify_resolved("yes please", "discord")
    assert "got it" in fake.sends()[-1][1]
    mirror.notify_resolved("no", "web")           # resolved on the OTHER surface → told so
    assert "web" in fake.sends()[-1][1]
    mirror.notify_timeout("wait for Monday")
    assert "no answer" in fake.sends()[-1][1] and "wait for Monday" in fake.sends()[-1][1]


def test_mirror_reply_resolves_the_blocking_ask(make_routine, scripted, monkeypatch):

    fake = _FakeDiscord(replies=[[], ["approved — go"]])
    monkeypatch.setattr(notify.utils_lib, "run_util", fake.run_util)
    monkeypatch.setattr(notify.utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(decisions, "DISCORD_POLL_S", 0)
    # the routine has the discord capability switched on (the doc covers the conduct)
    d = make_routine(slug="viaphone", budgets={"ask_timeout_min": 1})
    server = _server(d)
    server.permissions_home.mkdir(parents=True, exist_ok=True)
    (server.permissions_home / "communication.md").write_text(
        "---\ntags: [a, b, c]\nrequires:\n  utils: [discord]\n---\n"
        "# permission: communication — discord\nbody\n", encoding="utf-8")
    import yaml as _yaml
    cfg = _yaml.safe_load((d / "routine.yaml").read_text())
    cfg["permissions"] = ["communication"]
    cfg["capabilities"] = {"utils": ["discord"]}
    (d / "routine.yaml").write_text(_yaml.safe_dump(cfg))
    scripted([
        {"say": "q", "kind": "ask_user", "question": "Go?", "mode": "blocking"},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    ans = next(e for e in _events(run_dir) if e["type"] == "answer")
    assert ans["payload"]["text"] == "approved — go"
    assert ans["payload"]["source"] == "discord"
    assert not list((d / "questions" / "pending").glob("*.json"))
    assert any("got it" in a[1] for a in fake.sends())   # the channel was told it counted


def test_reply_texts_parses_tolerantly():
    assert decisions._reply_texts('["a", "b"]') == ["a", "b"]
    assert decisions._reply_texts('[{"text": "x"}, {"content": "y"}]') == ["x", "y"]
    assert decisions._reply_texts('{"messages": [{"text": "z"}]}') == ["z"]
    assert decisions._reply_texts("") == []
    assert decisions._reply_texts("not json") == []
    assert decisions._reply_texts('[{"foo": 1}]') == []
