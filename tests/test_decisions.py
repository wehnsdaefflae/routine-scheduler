"""The unified decision surface: one record shape for every kind of required feedback,
timeout-continues-on-default, and the Discord mirror's synchronization behavior."""

from __future__ import annotations

import json
import threading
import time

from conftest import finish

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
    perms = tmp_path / "test-library" / "permissions"
    perms.mkdir(parents=True, exist_ok=True)
    (perms / "util-authoring.md").write_text(
        "---\ntags: [a, b, c]\ngrants:\n  actions: [write_util]\n  confirm: true\n---\n"
        "# permission: util-authoring — user-approved\nbody\n", encoding="utf-8")
    d = make_routine(slug="approval", budgets={"ask_timeout_min": 0})
    scripted([
        {"say": "new util", "kind": "write_util", "name": "frob",
         "content": '"""frob — test util.\n\nusage: gu frob\ntags: test, demo\n"""\n'},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    q = next(e for e in _events(run_dir) if e["type"] == "question")
    assert q["payload"]["type"] == "util-approval"
    assert "NOT applied until approved" in q["payload"]["default"]
    rec = read_json(next(iter((d / "questions" / "pending").glob("*.json"))))
    assert rec["type"] == "util-approval"


# ------------------------------------------------------------------ the Discord mirror


class _FakeDiscord:
    """Records every discord util call; feeds back scripted read replies."""

    def __init__(self, replies=()):
        self.calls: list[list[str]] = []
        self.replies = list(replies)

    def run_util(self, home, name, args, timeout=0):
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
    monkeypatch.setattr(decisions.utils_lib, "run_util", fake.run_util)
    monkeypatch.setattr(decisions.utils_lib, "exists", lambda home, name: True)
    ctx = _mirror_ctx(make_routine, "nomirror", granted=False)
    assert decisions.mirror_blocking(ctx, "q1", "Go?", [], "", 8) is None
    assert fake.calls == []                       # never touches the channel ungranted


def test_mirror_sends_polls_and_notifies(make_routine, monkeypatch):
    # one empty read (cursor prime), then a reply on the first poll
    fake = _FakeDiscord(replies=[[], [{"text": "yes please"}]])
    monkeypatch.setattr(decisions.utils_lib, "run_util", fake.run_util)
    monkeypatch.setattr(decisions.utils_lib, "exists", lambda home, name: True)
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
    from rsched.engine import interact

    fake = _FakeDiscord(replies=[[], ["approved — go"]])
    monkeypatch.setattr(decisions.utils_lib, "run_util", fake.run_util)
    monkeypatch.setattr(decisions.utils_lib, "exists", lambda home, name: True)
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
