"""The unified decision surface: one record shape for every kind of required feedback, and
timeout-continues-on-default. The console is the ONLY surface — 0.230.0 deleted the Discord
mirror (D48/F193) and with it every engine-implicit outbound send."""

from __future__ import annotations

import threading
import time

import pytest

from conftest import finish
from rsched import utils_lib
from rsched.config import ServerConfig
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
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


@pytest.mark.flaky(reruns=2)   # two real threads race a 60s ask window; starves under xdist load
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


def test_util_secret_gate_files_one_request_covering_the_run(make_routine, scripted,
                                                             monkeypatch):
    """D39 through the four-state grant model: the FIRST util call declaring a store
    secret files ONE blocking ACCESS REQUEST (`secret:<NAME>` entities, record type
    `request`); an allow-now decision runs the util and covers every later call THIS run
    — no re-ask, and NOTHING persisted (a forever decision is the WEB layer's write, at
    click time; the engine never touches routine.yaml)."""
    from rsched import secrets as secrets_mod

    ran = []
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None,
                        **_kw: (ran.append((name, list(args))) or (0, "ran", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "util_needs",
                        lambda home, name: ({"FOO_KEY"}, False, set()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x"})
    d = make_routine(slug="secgate", budgets={"ask_timeout_min": 1})

    def answer_soon():
        deadline = time.time() + 5
        while time.time() < deadline:
            recs = list((d / "questions" / "pending").glob("*.json"))
            if recs:
                rec = read_json(recs[0])
                assert rec["type"] == "request" and rec["request"] == ["secret:FOO_KEY"]
                atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                                  {"qid": rec["qid"], "decision": "allow_now",
                                   "text": "allow now", "source": "web"})
                return
            time.sleep(0.02)

    th = threading.Thread(target=answer_soon)
    th.start()
    scripted([
        {"say": "call it", "kind": "util", "name": "frob", "args": []},
        {"say": "call it again", "kind": "util", "name": "frob", "args": []},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    th.join()
    assert status == "ok"
    events = _events(run_dir)
    questions = [e for e in events if e["type"] == "question"]
    assert len(questions) == 1 and questions[0]["payload"]["request"] == ["secret:FOO_KEY"]
    assert next(e for e in events if e["type"] == "answer")["payload"]["decision"] == "allow_now"
    assert ran == [("frob", []), ("frob", [])]        # both calls ran, ONE decision
    import yaml as _yaml
    persisted = _yaml.safe_load((d / "routine.yaml").read_text())
    assert not persisted.get("grants")                # allow_now persists NOTHING


def test_optional_secret_never_asks_and_is_withheld(make_routine, scripted, monkeypatch):
    """F290/R314: an OPTIONAL (`?`-declared) secret files NO exposure ask and never blocks
    the call — the engine withholds it from the child env instead, and the observation
    names the undecided withheld secret so an auth-needing call can request it. The
    page-fetch case: a public fetch runs prompt-free."""
    from rsched import secrets as secrets_mod

    seen_withhold = []
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None,
                        withhold_secrets=None,
                        **_kw: (seen_withhold.append(set(withhold_secrets or set()))
                                or (0, "fetched", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "util_needs",
                        lambda home, name: ({"WEB_AUTH_SOURCES"}, True, {"WEB_AUTH_SOURCES"}))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"WEB_AUTH_SOURCES": "x"})
    d = make_routine(slug="optsec")
    scripted([
        {"say": "public fetch", "kind": "util", "name": "page-fetch", "args": ["https://x"]},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    events = _events(run_dir)
    assert not [e for e in events if e["type"] == "question"]      # nobody was asked
    assert seen_withhold == [{"WEB_AUTH_SOURCES"}]                 # env withheld instead
    obs = next(e for e in events if e["type"] == "observation"
               and e["payload"].get("kind") == "util")
    assert obs["payload"]["withheld_optional"] == {"undecided": ["WEB_AUTH_SOURCES"],
                                                   "denied": 0}


def test_secret_grant_row_covers_runs_without_asking(make_routine, scripted, monkeypatch):
    """A persisted `grants: {secret:<NAME>: true}` row — written by the WEB when the user
    clicked allow-forever (or set on the routine page) — runs the util with NO question
    at all: once the user has said yes durably, the routine never re-asks."""
    from rsched import secrets as secrets_mod

    ran: list[tuple[str, list]] = []
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None,
                        **_kw: (ran.append((name, list(args))) or (0, "ran", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "util_needs", lambda home, name: ({"FOO_KEY"}, False, set()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x"})
    d = make_routine(slug="secgate2", budgets={"ask_timeout_min": 1})
    import yaml as _yaml
    cfg = _yaml.safe_load((d / "routine.yaml").read_text())
    cfg["grants"] = {"secret:FOO_KEY": True}
    (d / "routine.yaml").write_text(_yaml.safe_dump(cfg))

    scripted([{"say": "call", "kind": "util", "name": "frob", "args": []}, finish()])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    assert [e for e in _events(run_dir) if e["type"] == "question"] == []   # never asked
    assert ran == [("frob", [])]                                            # ran unprompted


def test_util_secret_gate_recorded_decline_refuses_without_asking(make_routine, scripted,
                                                                  monkeypatch):
    """D39: a routine whose `grants:` maps `secret:<NAME>` to false gets a refusing
    observation — the util never runs and NO question is filed (the mapping is the
    routine page's to change)."""
    from rsched import secrets as secrets_mod

    ran = []
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None, **_kw:
                        (ran.append((name, list(args))) or (0, "ran", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "util_needs",
                        lambda home, name: ({"FOO_KEY"}, False, set()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x"})
    d = make_routine(slug="secdeny")
    import yaml as _yaml
    cfg = _yaml.safe_load((d / "routine.yaml").read_text())
    cfg["grants"] = {"secret:FOO_KEY": False}
    (d / "routine.yaml").write_text(_yaml.safe_dump(cfg))
    scripted([
        {"say": "call it", "kind": "util", "name": "frob", "args": []},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    events = _events(run_dir)
    assert not any(e["type"] == "question" for e in events)
    obs = next(e for e in events if e["type"] == "observation"
               and e["payload"]["kind"] == "util")
    assert obs["payload"]["declined_secrets"] == ["FOO_KEY"]
    assert "routine page" in obs["payload"]["reason"]
    assert ran == []                                   # the util never executed


def test_secret_decline_observation_names_no_secrets(make_routine, scripted, monkeypatch):
    """R17: a DENIAL must not enumerate the names it refused — the model-facing reason
    and rendering carry a COUNT only (the transcript dict keeps the names for the user's
    own surfaces). The names are legitimately listed in every run's CAPABILITIES since
    0.119.0, so this is not secrecy — a refusal must simply never read as a consolation
    listing of exactly what the user just declined."""
    from rsched import secrets as secrets_mod
    from rsched.engine.observations import format_observation

    ran = []
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None,
                        **_kw: (ran.append(name) or (0, "ran", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "util_needs",
                        lambda home, name: ({"FOO_KEY", "BAR_KEY"}, False, set()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x", "BAR_KEY": "y"})
    d = make_routine(slug="secmute")
    import yaml as _yaml
    cfg = _yaml.safe_load((d / "routine.yaml").read_text())
    cfg["grants"] = {"secret:FOO_KEY": False, "secret:BAR_KEY": False}
    (d / "routine.yaml").write_text(_yaml.safe_dump(cfg))
    scripted([{"say": "call it", "kind": "util", "name": "frob", "args": []}, finish()])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    obs = next(e for e in _events(run_dir) if e["type"] == "observation"
               and e["payload"]["kind"] == "util")
    assert sorted(obs["payload"]["declined_secrets"]) == ["BAR_KEY", "FOO_KEY"]  # audit keeps them
    assert "FOO_KEY" not in obs["payload"]["reason"]
    assert "BAR_KEY" not in obs["payload"]["reason"]
    assert "2 secrets" in obs["payload"]["reason"]
    rendered = format_observation(obs["payload"])          # what the MODEL reads
    assert "FOO_KEY" not in rendered and "BAR_KEY" not in rendered
    assert "declined for 2 secrets" in rendered
    assert ran == []


def test_secret_decline_after_ask_stays_generic(make_routine, scripted, monkeypatch):
    """R17, the just-declined path: the user answers the exposure request with a deny —
    the refusing observation must not hand the names back either; it carries the count
    plus the shared decision phrase (deny_forever's 'never request it again')."""
    from rsched import secrets as secrets_mod
    from rsched.engine.observations import format_observation

    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None,
                        **_kw: (0, "ran", ""))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_lib, "util_needs", lambda home, name: ({"FOO_KEY"}, False, set()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x"})
    d = make_routine(slug="secmute2", budgets={"ask_timeout_min": 1})

    def decline_soon():
        deadline = time.time() + 5
        while time.time() < deadline:
            recs = list((d / "questions" / "pending").glob("*.json"))
            if recs:
                rec = read_json(recs[0])
                atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                                  {"qid": rec["qid"], "decision": "deny_forever",
                                   "text": "no", "source": "web"})
                return
            time.sleep(0.02)

    th = threading.Thread(target=decline_soon)
    th.start()
    scripted([{"say": "call it", "kind": "util", "name": "frob", "args": []}, finish()])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    th.join()
    assert status == "ok"
    obs = next(e for e in _events(run_dir) if e["type"] == "observation"
               and e["payload"]["kind"] == "util")
    assert obs["payload"]["declined_secrets"] == ["FOO_KEY"]   # transcript keeps the audit
    assert "FOO_KEY" not in obs["payload"]["reason"]
    assert "1 secret " in obs["payload"]["reason"]
    assert "declined permanently" in obs["payload"]["reason"]  # the shared phrase, no ids
    assert "FOO_KEY" not in format_observation(obs["payload"])


@pytest.mark.flaky(reruns=2)   # a real driver thread races the ask window; starves under xdist load
def test_ambiguous_approval_reply_is_held_not_consumed(make_routine, scripted):
    """D38: a reply that names neither option must not settle a blocking util-approval —
    it is HELD as a delayed user message (drained at the next turn boundary, i.e. after
    the decision), and the question stays open until a clear approve/decline arrives."""
    d = make_routine(slug="heldreply", budgets={"ask_timeout_min": 1})
    import yaml as _yaml
    cfg = _yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {"actions": ["write_util"], "confirm": "always"}
    (d / "routine.yaml").write_text(_yaml.safe_dump(cfg))

    def driver():
        """Answer twice: first something that names neither option, then a clear decline."""
        deadline = time.time() + 180
        sent = 0
        while time.time() < deadline and sent < 2:
            recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
            blocking = [r for r in recs if r.get("mode") == "blocking"]
            if blocking and not (d / "inbox" / f"answer-{blocking[0]['qid']}.json").exists():
                text = "Bin hier" if sent == 0 else "no thanks"
                atomic_write_json(d / "inbox" / f"answer-{blocking[0]['qid']}.json",
                                  {"qid": blocking[0]["qid"], "text": text, "source": "web"})
                sent += 1
            time.sleep(0.02)

    t = threading.Thread(target=driver)
    t.start()
    scripted([
        {"say": "new util", "kind": "write_util", "name": "frob",
         "content": '"""frob — test util.\n\nusage: gu frob\ntags: test, demo\nnet: none\n"""\n'},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    t.join()
    assert status == "ok"
    events = _events(run_dir)
    answers = [e["payload"] for e in events if e["type"] == "answer"]
    assert answers[0]["text"] == "Bin hier" and answers[0]["held"] is True
    assert answers[1]["text"] == "no thanks" and "held" not in answers[1]
    wu = next(e for e in events if e["type"] == "observation"
              and e["payload"]["kind"] == "write_util")
    assert wu["payload"]["declined"] and wu["payload"]["answer"] == "no thanks"
    # the held text reached the run as a NORMAL delayed message, after the decision
    assert any("Bin hier" in p.read_text(encoding="utf-8")
               for p in (run_dir / "consumed").glob("msg-*.json"))
    assert not list((d / "questions" / "pending").glob("*.json"))   # decline resolved it


def test_deferred_answer_reaches_the_live_run(make_routine, scripted):
    """F195: an answer to a question THIS run filed as deferred is injected at the next
    turn boundary. Before, it sat in the inbox for the NEXT run's digest while the live
    run finished claiming the question was still open (observed 2026-07-24)."""
    d = make_routine(slug="liveanswer")
    scripted([
        {"say": "q", "kind": "ask_user", "question": "Which color?", "mode": "deferred"},
        # the user answers on the Decisions page while the run is still going — the web
        # layer's exact answer-file shape, landing in the routine's own inbox
        {"say": "answer arrives", "kind": "write_file",
         "path": f"inbox/answer-q-{TS}-1.json",
         "content": {"qid": f"q-{TS}-1", "text": "blue", "source": "web",
                     "ts": "2026-07-08T07:01:00+00:00"}},
        {"say": "next turn", "kind": "read_file", "path": "main.md"},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    events = _events(run_dir)
    inj = [e["payload"]["text"] for e in events if e["type"] == "user_injection"]
    assert any("Which color?" in t and "blue" in t for t in inj), inj
    assert not list((d / "questions" / "pending").glob("*.json"))   # record consumed
    assert not list((d / "inbox").glob("answer-*.json"))            # answer consumed
