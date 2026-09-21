"""F447: scoped request decisions preserve authority across their real lifecycle."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from conftest import finish
from rsched.engine.availability import request_denial
from rsched.engine.requests import (
    apply_decision,
    apply_deferred_decisions,
    consume_once_grants,
)
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.entities import parse_entity
from rsched.grantpolicy import GrantPolicy
from rsched.paths import atomic_write_yaml, read_yaml
from rsched.policyload import load_policy
from rsched.web.grants_apply import apply_forever
from test_loop import _server
from test_requests import TS, _answer_when_asked, _ask, _loop, _once_loop, _reserve_discord


def call(verb=None, name="signal"):
    return {"kind": "util", "name": name, "args": [] if verb is None else [verb]}


@pytest.mark.parametrize("eid", ["util:signal:", "util::read", "util:signal:read:extra",
                                      "util:signal:bad_verb", "util:signal: read"])
def test_malformed(eid):
    assert parse_entity(eid) is None


def test_availability(tmp_path):
    assert parse_entity("util:signal:read") == ("util", "signal:read")
    g = GrantPolicy(gated_utils={"signal": ("messaging",)})
    assert request_denial(_loop(tmp_path, grants=g), _ask("util:signal:read")) == []
    for utils in ({"signal"}, {"signal:read"}):
        held = GrantPolicy(utils=frozenset(utils), gated_utils=g.gated_utils)
        assert "already enabled" in request_denial(
            _loop(tmp_path, grants=held), _ask("util:signal:read"))[0]
    narrow = GrantPolicy(utils=frozenset({"signal:read"}), gated_utils=g.gated_utils)
    assert request_denial(_loop(tmp_path, grants=narrow), _ask("util:signal")) == []
    for eid in ("util:signal", "util:signal:read"):
        denied = GrantPolicy(denied=frozenset({eid}), gated_utils=g.gated_utils)
        assert "PERMANENTLY declined" in request_denial(
            _loop(tmp_path, grants=denied), _ask("util:signal:read"))[0]


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("decision", ["allow_once", "allow_now"])
def test_decision_consumption_lifecycle(deferred, decision):
    loop = _once_loop(set())
    loop.base_grants = GrantPolicy(gated_utils={"signal": ("messaging",)})
    eid = "util:signal:read"
    if deferred:
        apply_deferred_decisions(loop, [{"request": [eid], "decision": decision}])
    else:
        apply_decision(loop, [eid], decision)
    assert loop.grants.deny(call("read")) is None
    assert loop.grants.deny(call("send"))
    for action in (call("send"), call(), call("read", "other")):
        assert consume_once_grants(loop, action, {"exit": 0}) == set()
    for obs in ({"declined_secrets": ["KEY"]}, {"missing": True}, {"error": "bad"}):
        assert consume_once_grants(loop, call("read"), obs) == set()
    assert eid in loop.ctx.granted_now
    spent = consume_once_grants(loop, call("read"), {"exit": 0})
    if decision == "allow_once":
        assert spent == {eid}
        assert not loop.ctx.granted_now and not loop.ctx.granted_once
        assert loop.grants.deny(call("read"))
    else:
        assert not spent and loop.grants.deny(call("read")) is None


def library(tmp_path):
    home = tmp_path / "lib" / "permissions"
    home.mkdir(parents=True)
    (home / "messaging.md").write_text(
        "---\nrequires:\n  utils: [signal, discord]\n  util_tags: [chat]\n---\nConduct\n")
    (home / "memory.md").write_text(
        "---\nrequires:\n  actions: [memory_read]\n---\nMemory conduct\n")
    routine = tmp_path / "routine"
    routine.mkdir()
    atomic_write_yaml(routine / "routine.yaml", {"permissions": [], "capabilities": {}})
    return SimpleNamespace(permissions_home=home), routine


def reload_policy(server, routine):
    raw = read_yaml(routine / "routine.yaml")
    return raw, load_policy(server.permissions_home, raw.get("permissions"),
                            raw.get("capabilities"), grants_map=raw.get("grants"))


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_forever_save_reload_no_widening(tmp_path, combined, reverse):
    server, routine = library(tmp_path)
    ids = ["util:signal:read", "action:memory_read"]
    if reverse:
        ids.reverse()
    if combined:
        apply_forever(server, routine, ids, "allow_forever")
    else:
        for eid in ids:
            apply_forever(server, routine, [eid], "allow_forever")
            reload_policy(server, routine)
    raw, g = reload_policy(server, routine)
    assert set(raw["capabilities"]["utils"]) == {"signal:read"}
    assert not raw["capabilities"].get("util_tags")
    assert g.deny(call("read")) is None
    assert g.deny(call("send")) and g.deny(call("read", "discord"))
    assert g.allows_kind("memory_read")
    assert "messaging" in raw["permissions"]
    apply_forever(server, routine, ["util:signal"], "allow_forever")
    assert reload_policy(server, routine)[1].deny(call("send")) is None


def test_narrow_doc_cannot_cover_broad_or_other_verb(tmp_path):
    server, routine = library(tmp_path)
    (server.permissions_home / "messaging.md").write_text(
        "---\nrequires:\n  utils: [signal:read]\n---\nConduct\n")
    apply_forever(server, routine, ["util:signal:read"], "allow_forever")
    for eid in ("util:signal", "util:signal:send"):
        with pytest.raises(HTTPException, match="no permission doc"):
            apply_forever(server, routine, [eid], "allow_forever")
    assert reload_policy(server, routine)[1].utils == frozenset({"signal:read"})


@pytest.mark.parametrize("decision", ["allow_now", "allow_once"])
def test_broad_deny_declines_expansion_not_existing_scope(tmp_path, decision):
    loop = _once_loop(set())
    loop.base_grants = GrantPolicy(gated_utils={"signal": ("messaging",)})
    apply_decision(loop, ["util:signal:read"], decision)
    apply_decision(loop, ["util:signal"], "deny_now")
    assert loop.grants.deny(call("read")) is None
    assert loop.grants.deny(call("send"))
    assert "THIS RUN" in request_denial(
        _loop(tmp_path, grants=loop.grants), _ask("util:signal:send"))[0]
    server, routine = library(tmp_path)
    apply_forever(server, routine, ["util:signal:read"], "allow_forever")
    apply_forever(server, routine, ["util:signal"], "deny_forever")
    raw, g = reload_policy(server, routine)
    assert raw["grants"] == {"util:signal": False}
    assert g.deny(call("read")) is None and g.deny(call("send"))


@pytest.mark.parametrize("decision", ["allow_now", "allow_once"])
def test_blocking_real_dispatch(make_routine, scripted, monkeypatch, decision):
    from rsched import utils_run

    d = make_routine(slug="scoped-dispatch", budgets={"ask_timeout_min": 1})
    server = _server(d)
    _reserve_discord(server)
    # Stub only the external subprocess, not decisions, validation, dispatch or spending.
    monkeypatch.setattr(utils_run, "run_util", lambda *a, **kw: (0, "sent", ""))
    util_dir = server.libraries_home / "utils" / "discord"
    util_dir.mkdir(parents=True)
    (util_dir / "main.py").write_text('"""discord — test\nusage: gu discord read\n"""\n')
    thread = _answer_when_asked(d, {"decision": decision, "text": decision})
    scripted([
        {"say": "ask", "kind": "ask_user", "mode": "blocking",
         "request": "util:discord:read", "question": "Read?", "default": "skip"},
        {"say": "wrong", **call("send", "discord")},
        {"say": "read", **call("read", "discord")},
        {"say": "again", **call("read", "discord")},
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    thread.join()
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    calls = [e["payload"] for e in events if e["type"] == "observation"
             and e["payload"].get("kind") == "util"]
    assert len(calls) == (1 if decision == "allow_once" else 2)
    assert all(c.get("exit") == 0 for c in calls)


@pytest.mark.parametrize("decision", ["allow_now", "allow_once", "allow_forever"])
def test_pending_scoped_answer_after_broad_denial(tmp_path, decision):
    loop = _once_loop(set())
    loop.base_grants = GrantPolicy(gated_utils={"signal": ("messaging",)})
    apply_decision(loop, ["util:signal"], "deny_now")
    apply_decision(loop, ["util:signal:read"], decision)
    assert loop.grants.deny(call("read")) is None
    assert loop.grants.deny(call("send"))
    assert "THIS RUN" in request_denial(
        _loop(tmp_path, grants=loop.grants), _ask("util:signal:send"))[0]


def test_forever_pending_answer_and_exact_tombstone(tmp_path):
    server, routine = library(tmp_path)
    apply_forever(server, routine, ["util:signal"], "deny_forever")
    apply_forever(server, routine, ["util:signal:read"], "allow_forever")
    _, g = reload_policy(server, routine)
    assert g.deny(call("read")) is None and g.deny(call("send"))
    apply_forever(server, routine, ["util:signal:read"], "deny_forever")
    _, g = reload_policy(server, routine)
    assert g.deny(call("read")) is None
    assert "PERMANENTLY declined" in request_denial(
        _loop(tmp_path, grants=g), _ask("util:signal:read"))[0]
