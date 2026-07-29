"""Typed access requests — the run side of the four-state grant model: request
validation inside the schema-retry cycle, the blocking decision flow (allow/deny ×
now/forever), deferred decisions consumed at boot, the run-scoped overlay's reach
(policy + schema + fs roots), and the prompt surfaces that teach it."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from conftest import finish
from rsched.engine.requests import request_denial
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.grants import GrantPolicy
from rsched.paths import atomic_write_json, read_json
from test_loop import _server

TS = "20260729-070000"


def _loop(tmp_path, *, grants=None, depth=0, machines=None, routine=None):
    ctx = SimpleNamespace(
        server=SimpleNamespace(libraries_home=tmp_path / "lib", machines=machines or {}),
        routine=routine or SimpleNamespace(grants={}, connections={}, machines=[],
                                           fs_read_roots=[], fs_write_roots=[],
                                           dir=tmp_path / "r"),
        depth=depth)
    return SimpleNamespace(ctx=ctx, allowed_tools=None,
                           grants=grants if grants is not None else GrantPolicy())


def _ask(request):
    return {"say": "s", "kind": "ask_user", "question": "why", "request": request}


# ------------------------------------------------------------ request_denial validation


def test_request_denial_ignores_non_requests(tmp_path):
    assert request_denial(_loop(tmp_path), {"say": "s", "kind": "util", "name": "x"}) == []
    assert request_denial(_loop(tmp_path), {"say": "s", "kind": "ask_user",
                                            "question": "plain ask"}) == []


def test_request_denial_blocks_subruns(tmp_path):
    problems = request_denial(_loop(tmp_path, depth=1), _ask("util:discord"))
    assert problems and "sub-workflows cannot request access" in problems[0]


def test_request_denial_teaches_the_grammar_on_a_bad_id(tmp_path):
    problems = request_denial(_loop(tmp_path), _ask("gibberish"))
    assert problems and "not a grant-entity id" in problems[0]
    # a base action kind is not an entity — the grammar correction covers it too
    assert request_denial(_loop(tmp_path), _ask("action:util"))


def test_request_denial_redirects_already_available_entities(tmp_path):
    g = GrantPolicy(actions=frozenset({"write_util"}),
                    utils=frozenset({"discord"}),
                    gated_utils={"discord": ("communication",)},
                    run_history="last")
    lp = _loop(tmp_path, grants=g)
    assert "already enabled" in request_denial(lp, _ask("action:write_util"))[0]
    assert "already enabled" in request_denial(lp, _ask("util:discord"))[0]
    assert "already covers" in request_denial(lp, _ask("runs:last"))[0]
    # a deeper depth than currently held stays requestable
    assert request_denial(lp, _ask("runs:all")) == []


def test_request_denial_names_unreserved_and_missing_utils(tmp_path):
    from rsched import utils_lib

    utils_lib.ensure_library(tmp_path / "lib")
    problems = request_denial(_loop(tmp_path), _ask("util:no-such-util"))
    assert problems and "nothing to unlock" in problems[0]


def test_request_denial_honors_tombstones_and_now_denials(tmp_path):
    tomb = GrantPolicy(denied=frozenset({"util:discord"}),
                       gated_utils={"discord": ("communication",)})
    assert "PERMANENTLY declined" in request_denial(_loop(tmp_path, grants=tomb),
                                                    _ask("util:discord"))[0]
    now = GrantPolicy(gated_utils={"discord": ("communication",)}).with_overlay(
        set(), {"util:discord"})
    assert "THIS RUN" in request_denial(_loop(tmp_path, grants=now), _ask("util:discord"))[0]
    granted = GrantPolicy(gated_utils={"discord": ("communication",)}).with_overlay(
        {"util:discord"}, set())
    assert "already granted for this run" in request_denial(
        _loop(tmp_path, grants=granted), _ask("util:discord"))[0]


def test_request_denial_guards_fs_entities(tmp_path):
    lp = _loop(tmp_path)
    assert "never grantable" in request_denial(lp, _ask("fs-read:~/.ssh"))[0]
    inside = str(lp.ctx.routine.dir / "state")
    assert "own working directory" in request_denial(lp, _ask(f"fs-write:{inside}"))[0]
    covered = SimpleNamespace(grants={}, connections={}, machines=[],
                              fs_read_roots=[], fs_write_roots=[tmp_path / "proj"],
                              dir=tmp_path / "r")
    assert "already covered" in request_denial(
        _loop(tmp_path, routine=covered), _ask(f"fs-write:{tmp_path / 'proj' / 'sub'}"))[0]
    assert request_denial(lp, _ask(f"fs-write:{tmp_path / 'elsewhere'}")) == []


def test_request_denial_checks_secret_connection_machine_registries(tmp_path, monkeypatch):
    from rsched import secrets as secrets_mod
    from rsched.oauth import store as oauth_store

    lp = _loop(tmp_path, machines={"omen": object()})
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: {"FOO_KEY": "x"})
    assert request_denial(lp, _ask("secret:FOO_KEY")) == []
    assert "not provisioned" in request_denial(lp, _ask("secret:MISSING_KEY"))[0]
    lp.ctx.routine.grants = {"secret:FOO_KEY": True}
    assert "already exposed" in request_denial(lp, _ask("secret:FOO_KEY"))[0]

    monkeypatch.setattr(oauth_store, "list_connections", list)
    assert "unknown connection provider" in request_denial(lp, _ask("connection:frobnitz"))[0]
    assert "no google account is connected" in request_denial(lp, _ask("connection:google"))[0]
    monkeypatch.setattr(oauth_store, "list_connections",
                        lambda: [{"provider": "google", "account": "a"},
                                 {"provider": "google", "account": "b"}])
    assert "several google accounts" in request_denial(lp, _ask("connection:google"))[0]
    monkeypatch.setattr(oauth_store, "list_connections",
                        lambda: [{"provider": "google", "account": "a"}])
    assert request_denial(lp, _ask("connection:google")) == []

    assert "no machine 'ghost'" in request_denial(lp, _ask("machine:ghost"))[0]
    assert request_denial(lp, _ask("machine:omen")) == []
    lp.ctx.routine.machines = ["omen"]
    assert "already bound" in request_denial(lp, _ask("machine:omen"))[0]


# ------------------------------------------------------------------ the decision flows


def _reserve_discord(server) -> None:
    server.permissions_home.mkdir(parents=True, exist_ok=True)
    (server.permissions_home / "communication.md").write_text(
        "---\ntags: [a, b, c]\nrequires:\n  utils: [discord]\n---\n"
        "# permission: communication — discord\nbody\n", encoding="utf-8")


def _answer_when_asked(d, payload: dict) -> threading.Thread:
    def answer_soon():
        deadline = time.time() + 5
        while time.time() < deadline:
            recs = list((d / "questions" / "pending").glob("*.json"))
            if recs:
                rec = read_json(recs[0])
                atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                                  {"qid": rec["qid"], "text": payload.get("text", ""),
                                   "source": "web", **payload})
                return
            time.sleep(0.02)
    t = threading.Thread(target=answer_soon)
    t.start()
    return t


def test_blocking_request_allow_now_unlocks_a_reserved_util(make_routine, scripted):
    """allow_now: the decision seeds the run overlay and rebuilds the live policy — the
    very next turn may CALL the just-granted reserved util (here it reaches dispatch and
    reports missing, since the test library is empty — a denial would have been a schema
    correction instead). Nothing is persisted."""
    d = make_routine(slug="reqallow", budgets={"ask_timeout_min": 1})
    server = _server(d)
    _reserve_discord(server)
    t = _answer_when_asked(d, {"decision": "allow_now", "text": "allow now"})
    scripted([
        {"say": "need the channel", "kind": "ask_user", "mode": "blocking",
         "request": "util:discord", "question": "May I post the digest to Discord?",
         "default": "publish to state/report.md only"},
        {"say": "post it", "kind": "util", "name": "discord", "args": ["send", "hi"]},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    t.join()
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    ask_obs = next(e for e in events if e["type"] == "observation"
                   and e["payload"].get("kind") == "ask_user")
    assert ask_obs["payload"]["decision"] == "allow_now"
    assert "THIS RUN" in ask_obs["payload"]["result"]
    util_obs = next(e for e in events if e["type"] == "observation"
                    and e["payload"].get("kind") == "util")
    assert util_obs["payload"].get("missing") is True     # dispatched, not denied
    import yaml as _yaml
    assert not (_yaml.safe_load((d / "routine.yaml").read_text()).get("grants"))
    assert not (_yaml.safe_load((d / "routine.yaml").read_text())
                .get("capabilities") or {}).get("utils")


def test_blocking_request_deny_now_keeps_the_gate_shut(make_routine, scripted):
    """deny_now: the util attempt after the decision is corrected inside the schema-retry
    cycle with the do-not-re-request wording — it never becomes a turn."""
    d = make_routine(slug="reqdeny", budgets={"ask_timeout_min": 1})
    server = _server(d)
    _reserve_discord(server)
    t = _answer_when_asked(d, {"decision": "deny_now", "text": "deny now"})
    scripted([
        {"say": "need the channel", "kind": "ask_user", "mode": "blocking",
         "request": "util:discord", "question": "May I post to Discord?",
         "default": "work without it"},
        {"say": "try anyway", "kind": "util", "name": "discord", "args": ["send", "hi"]},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    t.join()
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    schema_errors = [e for e in events if e["type"] == "error"
                     and e["payload"].get("where") == "schema"]
    assert any("THIS RUN" in e["payload"]["message"] for e in schema_errors)
    assert not any(e["type"] == "observation" and e["payload"].get("kind") == "util"
                   for e in events)


def test_free_text_on_a_request_is_held_then_a_decision_settles(make_routine, scripted):
    """D38 extended to requests: a reply that is not one of the four typed decisions is
    HELD as a delayed user message (the question stays open) — only a decision settles
    the request. The held text arrives as a normal message after the decision."""
    d = make_routine(slug="reqhold", budgets={"ask_timeout_min": 1})
    server = _server(d)
    _reserve_discord(server)

    def two_phase():
        deadline = time.time() + 5
        rec = None
        while time.time() < deadline:
            recs = list((d / "questions" / "pending").glob("*.json"))
            if recs:
                rec = read_json(recs[0])
                break
            time.sleep(0.02)
        assert rec is not None
        atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                          {"qid": rec["qid"], "text": "hm, why though?", "source": "web"})
        # wait until the engine HOLDS it (consumes the file), then decide properly
        deadline = time.time() + 5
        while time.time() < deadline:
            if not (d / "inbox" / f"answer-{rec['qid']}.json").exists():
                break
            time.sleep(0.02)
        atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                          {"qid": rec["qid"], "decision": "deny_now", "text": "deny now",
                           "source": "web"})

    t = threading.Thread(target=two_phase)
    t.start()
    scripted([
        {"say": "need it", "kind": "ask_user", "mode": "blocking",
         "request": "util:discord", "question": "May I?", "default": "skip it"},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    t.join()
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    held = [e for e in events if e["type"] == "answer" and e["payload"].get("held")]
    assert held and held[0]["payload"]["text"] == "hm, why though?"
    ask_obs = next(e for e in events if e["type"] == "observation"
                   and e["payload"].get("kind") == "ask_user")
    assert ask_obs["payload"].get("decision") == "deny_now"


def test_deferred_decision_applies_at_next_boot(make_routine, scripted):
    """An allow-now decided on the Decisions page between runs grants exactly the next
    run: the boot consumes the answer file, seeds the overlay BEFORE the prompt is
    composed, and the reserved util dispatches without a denial."""
    d = make_routine(slug="reqboot", budgets={"ask_timeout_min": 1})
    server = _server(d)
    _reserve_discord(server)
    pending = d / "questions" / "pending"
    pending.mkdir(parents=True)
    atomic_write_json(pending / "q-1.json",
                      {"qid": "q-1", "question": "May I post to Discord?", "options": [],
                       "asked": "20260728-070000", "mode": "deferred", "type": "request",
                       "request": ["util:discord"]})
    atomic_write_json(d / "inbox" / "answer-q-1.json",
                      {"qid": "q-1", "decision": "allow_now", "text": "allow now",
                       "source": "web"})
    scripted([
        {"say": "post it", "kind": "util", "name": "discord", "args": ["send", "hi"]},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    util_obs = next(e for e in events if e["type"] == "observation"
                    and e["payload"].get("kind") == "util")
    assert util_obs["payload"].get("missing") is True     # dispatched, not denied
    assert not list((d / "inbox").glob("answer-*.json"))  # consumed at boot


# ------------------------------------------------------------------ overlay reach


def test_run_context_effective_roots_carry_fs_grants(tmp_path):
    from rsched.config import ServerConfig
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript

    routine = SimpleNamespace(slug="r", dir=tmp_path / "r",
                              fs_read_roots=[tmp_path / "cfg-read"],
                              fs_write_roots=[])
    (tmp_path / "r").mkdir()
    ctx = RunContext(routine=routine, server=ServerConfig(), registry=None,
                     run_ts=TS, run_dir=tmp_path / "r" / "runs" / TS,
                     transcript=Transcript(tmp_path / "t.jsonl"),
                     budgets=Budgets(max_turns=1, max_wall_clock_min=1,
                                     max_total_tokens=1, max_subruns=1,
                                     max_subrun_depth=1, ask_timeout_min=1))
    ctx.granted_now.update({f"fs-read:{tmp_path / 'granted-read'}",
                            f"fs-write:{tmp_path / 'granted-write'}",
                            "util:discord"})
    assert tmp_path / "cfg-read" in ctx.read_roots()
    assert tmp_path / "granted-read" in ctx.read_roots()
    assert tmp_path / "granted-write" in ctx.write_roots()
    assert all("discord" not in str(p) for p in ctx.write_roots())


def test_with_overlay_folds_capability_entities():
    g = GrantPolicy().with_overlay(
        {"action:memory_read", "util:discord", "runs:last", "workflows:generate",
         "secret:FOO_KEY"}, {"util:usenet"})
    assert g.allows_kind("memory_read")
    assert "discord" in g.utils
    assert g.run_history == "last" and g.workflows == "generate"
    assert g.entity_state("secret:FOO_KEY") == "granted_now"
    assert g.entity_state("util:usenet") == "denied_now"
    # base+overlay, never stacked: re-applying over the ORIGINAL base drops old grants
    assert GrantPolicy().with_overlay(set(), set()).utils == frozenset()


def test_capabilities_digest_teaches_once_grants_and_tombstones(make_routine, tmp_path):
    from rsched.config import ServerConfig, load_routine
    from rsched.engine.capabilities import capabilities_digest
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript

    d = make_routine(slug="digestr")
    cfg, _ = load_routine(d)
    server = ServerConfig()
    server.libraries_home = tmp_path / "lib"
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts=TS,
                     run_dir=d / "runs" / TS,
                     transcript=Transcript(tmp_path / "t2.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy(gated_utils={"discord": ("communication",)},
                             denied=frozenset({"util:discord"})).with_overlay(
        {"fs-write:/tmp/granted"}, set())
    from rsched import utils_lib
    utils_lib.ensure_library(server.libraries_home)
    digest = capabilities_digest(ctx)
    assert "Granted for THIS RUN only" in digest
    assert "fs-write:/tmp/granted" in digest
