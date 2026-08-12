"""Typed access requests — the run side of the grant model: request validation inside
the schema-retry cycle, the blocking decision flow (allow/deny × now/forever, plus
allow-once for turn-action classes — D65), deferred decisions consumed at boot, the
run-scoped overlay's reach (policy + schema + fs roots), and the prompt surfaces that
teach it."""

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


def test_blocking_request_allow_once_spends_on_the_next_matching_dispatch(make_routine,
                                                                          scripted):
    """D65 end-to-end: an allow_once decision unlocks the gated kind for EXACTLY one
    dispatched use — the first schedule_run arms its one-shot (a real execution), the
    second is corrected inside the schema-retry cycle (revoked, never a turn)."""
    d = make_routine(slug="reqonce", budgets={"ask_timeout_min": 1})
    server = _server(d)
    t = _answer_when_asked(d, {"decision": "allow_once", "text": "allow once"})
    scripted([
        {"say": "need a one-shot", "kind": "ask_user", "mode": "blocking",
         "request": "action:schedule_run", "question": "May I arm a follow-up run?",
         "default": "note the need in the summary"},
        {"say": "arm it", "kind": "schedule_run", "target": "reqonce",
         "fire_at": "+3d", "reason": "follow up on the digest"},
        {"say": "arm another", "kind": "schedule_run", "target": "reqonce",
         "fire_at": "+5d", "reason": "and again"},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    t.join()
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    ask_obs = next(e for e in events if e["type"] == "observation"
                   and e["payload"].get("kind") == "ask_user")
    assert ask_obs["payload"]["decision"] == "allow_once"
    assert "ONE action" in ask_obs["payload"]["result"]
    armed = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "schedule_run"]
    assert len(armed) == 1                      # the second attempt never became a turn
    schema_errors = [e["payload"]["message"] for e in events if e["type"] == "error"
                     and e["payload"].get("where") == "schema"]
    assert any("switched OFF" in m for m in schema_errors)   # revoked → gated again
    import yaml as _yaml
    persisted = (_yaml.safe_load((d / "routine.yaml").read_text())
                 .get("capabilities") or {}).get("actions") or []
    assert "schedule_run" not in persisted       # nothing persisted


def test_a_missing_util_does_not_spend_a_once_grant(make_routine, scripted):
    """The grant is spent by USE, not by attempt: a dispatched call the handler bounced
    (here: the util does not exist in the test library) leaves the once-grant armed, so
    the retry is still permitted."""
    d = make_routine(slug="reqoncemiss", budgets={"ask_timeout_min": 1})
    server = _server(d)
    _reserve_discord(server)
    t = _answer_when_asked(d, {"decision": "allow_once", "text": "allow once"})
    scripted([
        {"say": "need the channel", "kind": "ask_user", "mode": "blocking",
         "request": "util:discord", "question": "May I post once?",
         "default": "skip the post"},
        {"say": "post it", "kind": "util", "name": "discord", "args": ["send", "hi"]},
        {"say": "retry", "kind": "util", "name": "discord", "args": ["send", "hi again"]},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    t.join()
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    calls = [e for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "util"]
    assert len(calls) == 2                       # both dispatched — neither was denied
    assert all(c["payload"].get("missing") for c in calls)


def _once_loop(granted, home=None, rdir=None):
    from pathlib import Path
    ctx = SimpleNamespace(granted_now=set(granted), denied_now=set(),
                          granted_once=set(granted), grant_args={}, run_ts=TS,
                          routine=SimpleNamespace(dir=rdir or Path("/nonexistent-routine")),
                          server=SimpleNamespace(
                              libraries_home=home or Path("/nonexistent-lib")))
    return SimpleNamespace(ctx=ctx, base_grants=GrantPolicy(), allowed_tools=None,
                           grants=GrantPolicy(), _finish_reserved=False,
                           action_schema=None)


def test_consume_once_spends_on_use_and_survives_gate_refusals():
    from rsched.engine.requests import consume_once_grants, spent_notice

    loop = _once_loop({"util:discord"})
    # a user gate refused the call pre-execution — still armed
    assert consume_once_grants(loop, {"kind": "util", "name": "discord"},
                               {"kind": "util", "declined_secrets": ["X"]}) == set()
    # an unrelated action — still armed
    assert consume_once_grants(loop, {"kind": "read_file", "path": "state/x"}, {}) == set()
    assert "util:discord" in loop.ctx.granted_once
    # the real use spends it: gone from BOTH overlay sets, policy rebuilt without it
    spent = consume_once_grants(loop, {"kind": "util", "name": "discord"},
                                {"kind": "util", "name": "discord", "exit": 0})
    assert spent == {"util:discord"}
    assert loop.ctx.granted_once == set() and loop.ctx.granted_now == set()
    assert "discord" not in loop.grants.utils
    assert "ONCE-GRANT SPENT" in spent_notice(spent)


def test_once_match_covers_each_turn_action_class():
    from rsched.engine.requests import _once_match

    ctx = _once_loop(set()).ctx
    assert _once_match("action:memory_read", {"kind": "memory_read", "name": "x"}, ctx)
    assert not _once_match("action:memory_read", {"kind": "memory_write", "name": "x"}, ctx)
    assert _once_match("util:discord", {"kind": "util", "name": "discord"}, ctx)
    assert not _once_match("util:discord", {"kind": "util", "name": "other"}, ctx)
    # runs: spent by reading ANOTHER run's tree — never by the run's own
    assert _once_match("runs:last",
                       {"kind": "read_file", "path": "runs/20260101-000000/result.md"}, ctx)
    assert not _once_match("runs:last",
                           {"kind": "read_file", "path": f"runs/{TS}/state.json"}, ctx)
    assert not _once_match("runs:last", {"kind": "read_file", "path": "state/notes.md"}, ctx)
    assert _once_match("workflows:generate",
                       {"kind": "subtask", "workflow": "generate", "prompt": "p"}, ctx)
    assert not _once_match("workflows:generate",
                           {"kind": "subtask", "workflow": "general-task", "prompt": "p"}, ctx)


def test_apply_decision_allow_once_arms_once_classes_only():
    """Engine fail-closed mirror of the web guard: allow_once on a class outside
    entities.ONCE_CLASSES grants NOTHING (never a silent widening to allow_now — an
    unconsumable once-grant would revoke nothing all run). secret:/fs-*: ARE
    once-grantable since D76."""
    from rsched.engine.requests import apply_decision

    loop = _once_loop(set())
    apply_decision(loop, ["connection:google"], "allow_once")
    assert loop.ctx.granted_now == set() and loop.ctx.granted_once == set()
    apply_decision(loop, ["util:discord"], "allow_once")
    assert loop.ctx.granted_now == {"util:discord"}
    assert loop.ctx.granted_once == {"util:discord"}
    apply_decision(loop, ["secret:FOO_KEY", "fs-write:/tmp/somewhere"], "allow_once")
    assert {"secret:FOO_KEY", "fs-write:/tmp/somewhere"} <= loop.ctx.granted_once


def _seed_util(home, name, docstring):
    d = home / "utils" / name
    d.mkdir(parents=True)
    (d / "main.py").write_text(f'"""{docstring}"""\n', encoding="utf-8")


def test_consume_once_secret_spent_only_by_a_declaring_util(tmp_path):
    """D76: a once-granted secret is spent by the next util call whose script declares
    it (the injection surface — utils_lib.util_needs, calls: tree included); an
    undeclaring util neither receives nor spends it."""
    from rsched.engine.requests import consume_once_grants

    _seed_util(tmp_path, "withsec", "withsec — x\nusage: gu withsec\nsecrets: FOO_KEY\n")
    _seed_util(tmp_path, "nosec", "nosec — x\nusage: gu nosec\n")
    loop = _once_loop({"secret:FOO_KEY"}, home=tmp_path)
    obs = {"kind": "util", "exit": 0}
    assert consume_once_grants(loop, {"kind": "util", "name": "nosec"}, obs) == set()
    assert "secret:FOO_KEY" in loop.ctx.granted_once
    spent = consume_once_grants(loop, {"kind": "util", "name": "withsec"}, obs)
    assert spent == {"secret:FOO_KEY"}
    assert loop.ctx.granted_now == set() and loop.ctx.granted_once == set()


def test_consume_once_fs_spent_by_file_action_under_root_or_any_util(tmp_path):
    """D76: a once-granted fs root is spent by a file action under it (relative paths
    resolve to the routine dir and do NOT touch it) or by ANY util invocation — the
    sandbox mounts granted roots wholesale; the approved coarser promise."""
    from rsched.engine.requests import consume_once_grants

    root = tmp_path / "proj"
    eid = f"fs-write:{root}"
    loop = _once_loop({eid}, rdir=tmp_path / "routine")
    assert consume_once_grants(loop, {"kind": "read_file", "path": "state/x"}, {}) == set()
    assert eid in loop.ctx.granted_once
    spent = consume_once_grants(loop, {"kind": "edit_file", "path": f"{root}/notes.md",
                                       "anchor": "a", "replacement": "b"}, {})
    assert spent == {eid}
    # any util invocation receives the mounted root, so it spends the grant too
    loop2 = _once_loop({eid}, rdir=tmp_path / "routine")
    spent2 = consume_once_grants(loop2, {"kind": "util", "name": "whatever"},
                                 {"kind": "util", "exit": 0})
    assert spent2 == {eid}


def test_child_inheritance_excludes_once_armed_resources():
    """A once-armed grant is narrower than the run, so it never flows to a child run
    (which would spend 'one action' many times over) — D76."""
    from rsched.engine.childrun import inheritable_resources

    granted = {"secret:FOO_KEY", "fs-read:/tmp/x", "util:discord"}
    assert inheritable_resources(granted, set()) == {"secret:FOO_KEY", "fs-read:/tmp/x"}
    assert inheritable_resources(granted, {"secret:FOO_KEY"}) == {"fs-read:/tmp/x"}


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
    """D38 extended to requests: a reply that is not one of the typed decisions is
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


def test_request_ids_canonicalize_fs_paths(monkeypatch, tmp_path):
    """Ids are canonicalized where the request enters the pipeline: an fs entity asked
    as `~/…` must yield the SAME absolute id in the pending record, the web's config
    write and the run overlay — or the granted root never matches what the enforcers
    compare (write_roots/read_roots hold expanded absolute paths)."""
    from rsched.engine.requests import request_ids

    monkeypatch.setenv("HOME", str(tmp_path))
    assert request_ids({"request": "fs-write:~/proj"}) == [f"fs-write:{tmp_path / 'proj'}"]
    # non-fs ids pass unchanged; a malformed id passes RAW so request_denial can teach it
    assert request_ids({"request": ["util:discord", "gibberish"]}) \
        == ["util:discord", "gibberish"]


def test_web_decision_mid_run_bridges_into_the_live_overlay(make_routine, scripted,
                                                            tmp_path, monkeypatch):
    """R118: a deferred access request answered on the Decisions page WHILE the run is
    live grants the RUNNING run, not just the next one. Before the drain bridge only the
    answer's prose ("usable now") reached the model — ctx.granted_now never updated, so
    file actions kept rejecting the path and every later util call's sandbox was built
    without the new root (EACCES until the next run rebuilt it from config)."""
    from rsched.engine import executor as executor_mod

    target = tmp_path / "granted-target"
    target.mkdir()
    policies = []
    monkeypatch.setattr(executor_mod.utils_lib, "run_util",
                        lambda home, name, args, timeout=0, policy=None, extra_secrets=None,
                        **_kw: (policies.append(policy) or (0, "ran", "")))
    monkeypatch.setattr(executor_mod.utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(executor_mod.utils_lib, "util_needs",
                        lambda home, name: (set(), False, set()))
    d = make_routine(slug="livegrant")
    qid = f"q-{TS}-2"
    scripted([
        {"say": "try before the grant", "kind": "write_file",
         "path": str(target / "before.txt"), "content": "x"},
        {"say": "ask for the root", "kind": "ask_user", "mode": "deferred",
         "question": "May I write the media library?", "request": f"fs-write:{target}"},
        # the user clicks allow-forever on the Decisions page while the run is LIVE —
        # the web layer persists config at click time (not emulated here: the ENGINE
        # must not depend on it) and files exactly this answer shape into the inbox
        {"say": "the web's answer file lands", "kind": "write_file",
         "path": f"inbox/answer-{qid}.json",
         "content": {"qid": qid, "decision": "allow_forever", "source": "web",
                     "text": "allowed permanently — recorded in the routine's config "
                             "(and usable now)", "ts": "2026-07-29T07:01:00+00:00"}},
        {"say": "try after the grant", "kind": "write_file",
         "path": str(target / "after.txt"), "content": "it works now"},
        {"say": "the sandbox too", "kind": "util", "name": "frob", "args": []},
        finish(),
    ])
    status, run_dir = run_routine(d, _server(d), run_ts=TS)
    assert status == "ok"
    # before the decision: rejected (outside every root); after: written
    assert not (target / "before.txt").exists()
    assert (target / "after.txt").read_text(encoding="utf-8") == "it works now"
    events = read_events(run_dir / "transcript.jsonl")[0]
    before = next(e for e in events if e["type"] == "observation"
                  and e["payload"].get("kind") == "write_file")
    assert before["payload"].get("error")
    # the very next util call's sandbox already carries the granted root
    assert policies and any(target in (p.write_roots or ()) for p in policies)
    # the bridge carries the RUNTIME grant only — the engine never writes config
    import yaml as _yaml
    raw = _yaml.safe_load((d / "routine.yaml").read_text())
    assert str(target) not in str(raw.get("fs_write_roots") or [])


def test_reserved_finish_schema_survives_a_drain_time_decision():
    """A decision bridged at the same boundary that spent the reserved finish turn must
    not un-narrow the finish-only grammar — the last turn stays a finish; the policy
    update itself still lands (resource consumers read it)."""
    from rsched.engine.requests import rebuild_policy

    sentinel = {"narrowed": "finish-only"}
    ctx = SimpleNamespace(granted_now={"util:discord"}, denied_now=set())
    loop = SimpleNamespace(ctx=ctx, base_grants=GrantPolicy(), allowed_tools=None,
                           action_schema=sentinel, _finish_reserved=True)
    rebuild_policy(loop)
    assert loop.action_schema is sentinel          # the narrowed grammar survived
    assert "discord" in loop.grants.utils          # the grant itself still landed
    loop._finish_reserved = False
    rebuild_policy(loop)
    assert loop.action_schema is not sentinel      # normal turns re-project as before


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
