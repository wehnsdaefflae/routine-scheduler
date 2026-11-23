"""The root command check (engine/rootcheck.py): while a run holds a root login, its routine's
`root_check` picks what the decision model rates — the remote commands sent there, every tool
call, or nothing — with the five calls before it; a risky call waits for the operator, whose
answer can also switch the mode for this run or from now on."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from helpers import run_context, server_for
from rsched import machine_privilege, rootmodes, secrets
from rsched.config import MachineConfig
from rsched.config.decisionconf import DecisionEndpointConfig
from rsched.endpoints.base import EndpointError
from rsched.endpoints.decisions import Decision, DecisionAnswer
from rsched.engine import rootcheck
from rsched.engine.exec_env import _extra_secrets
from rsched.engine.observations import format_observation
from rsched.web.root_check_answer import apply_general

TS = "20261123-120000"


def _call(verb="exec", machine="root-box", *rest):
    return {"say": "s", "kind": "util", "name": "remote", "args": [verb, machine, *rest]}


def _run(make_routine, monkeypatch, *, mode="remote", p_risky=0.1, error="", depth=0,
         privileged=None, slug="rooted"):
    """A real run context holding one root-login machine, under `mode`, with a stubbed decision
    model; returns (loop, the evidence the model was shown)."""
    d = make_routine(slug=slug)
    mac = MachineConfig(name="root-box", host="10.0.0.9", user="mark", key_var="K")
    server = server_for(d, machines={"root-box": mac}, decision_endpoints={
        "ep": DecisionEndpointConfig(name="ep", protocol="systemone", api_key="k", timeout_s=5)})
    if privileged is not None:
        machine_privilege.record(server.routines_home, "root-box", mac, privileged=privileged,
                                 why="probed")
    ctx = run_context(d, TS, server=server, depth=depth)
    ctx.routine.root_check = mode
    ctx.granted_now.add("machine:root-box")
    asked: list = []
    ref = SimpleNamespace(name="jev", endpoint="ep", model="m")

    def pick(server, name, *, images, roles=None):
        if error:
            raise EndpointError(error)
        return object(), ref

    def decide(endpoint, ref, evidence, images, questions, *, timeout, purpose):
        asked.append(evidence)
        return Decision(answers=[DecisionAnswer(name="risky", type="yes_no",
                                                probability=p_risky)])

    monkeypatch.setattr(rootcheck, "pick_decision_model", pick)
    monkeypatch.setattr(rootcheck, "decide", decide)
    return SimpleNamespace(ctx=ctx), asked


def _ask(answer, monkeypatch):
    filed: list = []

    def handle_ask(loop, action, poll_s, qtype="question", subject=""):
        filed.append((action, qtype))
        return answer

    monkeypatch.setattr(rootcheck, "handle_ask", handle_ask)
    return filed


def _made(ctx, *actions):
    """Record actions as the loop does: landed, then observed as having run."""
    for action in actions:
        ctx.transcript.event("assistant_action", action)
        ctx.transcript.event("observation", {"kind": action["kind"], "exit": 0})


@pytest.mark.parametrize(("args", "expect"), [
    (["exec", "m", "--command", "ls /"], ("m", "ls /")),
    (["exec", "m", "--command=whoami", "--cwd", "/etc"], ("m", "cd /etc; whoami")),
    (["submit", "m", "--command", "make"], ("m", "[submit] make")),
    (["push", "m", "--src", "a", "--dest", "/etc/b"], ("m", "[push] push (SFTP) a → /etc/b")),
    (["status", "m", "--job", "x"], None),
    (["exec"], None),
])
def test_the_remote_calls_it_covers(args, expect):
    assert rootcheck.gated_call({"kind": "util", "name": "remote", "args": args}) == expect
    assert rootcheck.gated_call({"kind": "util", "name": "other", "args": args}) is None


def test_every_warning_offers_each_other_mode_for_the_run_and_from_now_on():
    opts = rootmodes.options("remote")
    assert opts[:2] == ["approve", "decline"] and len(opts) == 6
    assert all(o.startswith("approve,") for o in opts[2:])
    assert {rootmodes.parse(o) for o in opts[2:]} == {
        ("any", "run"), ("any", "general"), ("off", "run"), ("off", "general")}
    assert rootmodes.parse("approve") is None


def test_remote_mode_rates_the_command_with_the_five_sent_there(make_routine, monkeypatch):
    loop, asked = _run(make_routine, monkeypatch, p_risky=0.04)
    filed = _ask({}, monkeypatch)
    _made(loop.ctx, *(_call("exec", "root-box", "--command", f"c{i}") for i in range(7)),
          {"kind": "write_file", "path": "x"})
    loop.ctx.transcript.event("assistant_action", _call("exec", "root-box", "--command", "no"))
    loop.ctx.transcript.event("observation", {"kind": "util", "error": "x", "root_check": "h"})
    assert rootcheck.gate(loop, _call("exec", "root-box", "--command", "ls")) is None
    assert asked[0]["command"] == "ls"
    assert asked[0]["previous_commands"] == ["c2", "c3", "c4", "c5", "c6"]
    assert rootcheck.gate(loop, {"kind": "write_file", "path": "y"}) is None
    assert len(asked) == 1 and not filed, "remote mode rates only remote commands"


def test_any_mode_rates_every_tool_call_but_not_the_runs_own_steering(make_routine, monkeypatch):
    loop, asked = _run(make_routine, monkeypatch, mode="any", p_risky=0.04)
    _made(loop.ctx, {"kind": "read_file", "path": "a"})
    assert rootcheck.gate(loop, {"kind": "write_file", "path": "/etc/x"}) is None
    assert "/etc/x" in asked[0]["action"] and asked[0]["root_machines"][0]["machine"] == "root-box"
    assert asked[0]["previous_actions"] == ['{"kind": "read_file", "path": "a"}']
    assert rootcheck.gate(loop, {"kind": "finish", "status": "ok"}) is None and len(asked) == 1


def test_off_or_no_root_login_rates_nothing(make_routine, monkeypatch):
    loop, asked = _run(make_routine, monkeypatch, mode="off", p_risky=0.99)
    assert rootcheck.gate(loop, _call("exec", "root-box", "--command", "reboot")) is None
    loop, asked = _run(make_routine, monkeypatch, mode="any", p_risky=0.99, privileged=False,
                       slug="unrooted")
    assert rootcheck.gate(loop, _call("exec", "root-box", "--command", "reboot")) is None
    assert not asked


@pytest.mark.parametrize(("answer", "ran"), [
    ({"answered": True, "answer": "approve"}, True),
    ({"answered": True, "answer": "decline"}, False),
    ({"qid": "q1"}, False),
])
def test_a_risky_call_waits_for_the_operator(make_routine, monkeypatch, answer, ran):
    loop, _ = _run(make_routine, monkeypatch, p_risky=0.93)
    filed = _ask(answer, monkeypatch)
    obs = rootcheck.gate(loop, _call("exec", "root-box", "--command", "rm -rf /srv"))
    (action, qtype), = filed
    assert qtype == "command-approval" and action["mode"] == "blocking"
    assert action["options"] == rootmodes.options("remote")
    assert "rm -rf /srv" in action["question"] and "P(risky) 0.93 by jev" in action["question"]
    assert (obs is None) is ran
    if not ran:
        assert format_observation(obs).startswith("OBSERVATION (util remote NOT run)")


def test_a_switch_chosen_at_a_warning_holds_for_the_rest_of_the_run(make_routine, monkeypatch):
    """The switch both approves the call and changes the mode; a resumed leg reads it back
    from the transcript's question and answer."""
    loop, asked = _run(make_routine, monkeypatch, p_risky=0.93)
    _ask({"answered": True, "answer": rootmodes.option("off", "run")}, monkeypatch)
    assert rootcheck.gate(loop, _call("exec", "root-box", "--command", "reboot")) is None
    assert rootcheck.run_mode(loop.ctx) == "off"
    assert rootcheck.gate(loop, _call("exec", "root-box", "--command", "reboot")) is None
    assert len(asked) == 1
    rootcheck._seen.clear()                                   # a resumed leg starts unread
    tr = loop.ctx.transcript
    tr.event("question", {"qid": "q9", "type": "command-approval", "question": "?"})
    tr.event("answer", {"qid": "q9", "text": rootmodes.option("any", "general")})
    assert rootcheck.run_mode(loop.ctx) == "any"


def test_no_verdict_is_treated_as_risky(make_routine, monkeypatch):
    loop, _ = _run(make_routine, monkeypatch, error="no decision model is set")
    filed = _ask({"answered": True, "answer": "decline"}, monkeypatch)
    obs = rootcheck.gate(loop, _call("exec", "root-box", "--command", "uptime"))
    assert obs is not None and "no risk verdict (no decision model is set)" in obs["error"]
    assert filed


def test_a_child_cannot_ask_so_a_risky_call_is_refused(make_routine, monkeypatch):
    loop, _ = _run(make_routine, monkeypatch, mode="any", p_risky=0.8, depth=1)
    filed = _ask({}, monkeypatch)
    obs = rootcheck.gate(loop, {"kind": "shell", "command": "reboot"})
    assert obs is not None and "cannot ask the operator" in obs["error"] and not filed
    assert format_observation(obs).startswith("OBSERVATION (shell NOT run)")


def test_from_now_on_is_written_by_the_web_and_for_this_run_is_not(tmp_path):
    (tmp_path / "routine.yaml").write_text("name: x\n", encoding="utf-8")
    record = {"type": "command-approval"}
    assert apply_general(None, record, tmp_path, rootmodes.option("off", "run")) == {}
    assert apply_general(None, {"type": "question"}, tmp_path,
                         rootmodes.option("off", "general")) == {}
    saved = apply_general(None, record, tmp_path, rootmodes.option("any", "general"))
    assert saved == {"root_check_saved": "any"}
    assert "root_check: any" in (tmp_path / "routine.yaml").read_text(encoding="utf-8")


def test_a_root_machines_key_reaches_only_the_call_the_check_sees(make_routine, monkeypatch):
    """A script, or a util that `calls: remote`, would carry a root login's key past the check:
    it gets the metadata but not the key — unless the check is off. An unprivileged machine's
    key is never held back."""
    loop, _ = _run(make_routine, monkeypatch)
    ctx = loop.ctx
    ctx.server.machines["agent-box"] = MachineConfig(name="agent-box", host="10.0.0.8",
                                                     user="agent", key_var="K2")
    machine_privilege.record(ctx.server.routines_home, "agent-box",
                             ctx.server.machines["agent-box"], privileged=False, why="uid 1001")
    ctx.granted_now.add("machine:agent-box")
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"K": "PEM-ROOT", "K2": "PEM-AGENT"})

    def keys(util):
        return json.loads(_extra_secrets(ctx, util)["RSCHED_MACHINE_KEYS"])

    assert keys("remote") == {"root-box": "PEM-ROOT", "agent-box": "PEM-AGENT"}
    assert keys("") == {"agent-box": "PEM-AGENT"}
    ctx.routine.root_check = "off"
    assert keys("") == {"root-box": "PEM-ROOT", "agent-box": "PEM-AGENT"}
