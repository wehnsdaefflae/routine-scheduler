"""D88 phase 1: per-routine procedures — the deterministic half of a routine. A
`procedures/<name>.py` PEP 723 script, private to the routine, run by the gated
`procedure` action inside the util sandbox/env contract (declared-only secrets, F290
optional withholding).
"""

from __future__ import annotations

import json

import yaml

from conftest import finish
from rsched import procedures, sandbox
from rsched.config import ServerConfig
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.grants import GATED_KINDS, GrantPolicy

TS = "20260708-070000"

SCRIPT = '''# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""probe — env and args probe for the procedures tests.

net: none
secrets: PROC_TOKEN, OPT_TOKEN?
"""
import json
import os
import sys

print(json.dumps({"args": sys.argv[1:],
                  "token": os.environ.get("PROC_TOKEN"),
                  "opt": os.environ.get("OPT_TOKEN"),
                  "other": os.environ.get("OTHER_KEY")}))
'''


def _routine(tmp_path):
    d = tmp_path / "r"
    (d / "procedures").mkdir(parents=True)
    (d / "procedures" / "probe.py").write_text(SCRIPT, encoding="utf-8")
    return d


def test_list_and_needs_from_own_header(tmp_path):
    d = _routine(tmp_path)
    procs = procedures.list_procedures(d)
    assert [p["name"] for p in procs] == ["probe"]
    assert "env and args probe" in procs[0]["summary"]
    declared, net, optional = procedures.needs(d, "probe")
    assert declared == {"PROC_TOKEN", "OPT_TOKEN"}
    assert optional == {"OPT_TOKEN"} and net is False
    assert procedures.list_procedures(tmp_path / "empty") == []


def test_run_procedure_scoped_env_and_withholding(tmp_path, monkeypatch):
    d = _routine(tmp_path)
    monkeypatch.setattr("rsched.secrets.load_secrets",
                        lambda: {"PROC_TOKEN": "t-1", "OPT_TOKEN": "o-1", "OTHER_KEY": "x"})
    monkeypatch.setenv("OTHER_KEY", "leaked-via-daemon-env")
    policy = sandbox.SandboxPolicy(mode="off")
    code, out, err = procedures.run_procedure(d, "probe", ["a", "b"], policy=policy,
                                              libraries_home=tmp_path / "lib")
    assert code == 0, err
    data = json.loads(out)
    # declared secrets injected, the undeclared store key scrubbed, args pass through
    assert data == {"args": ["a", "b"], "token": "t-1", "opt": "o-1", "other": None}
    # a withheld optional (F290) is scrubbed even though the store has it
    code, out, err = procedures.run_procedure(d, "probe", [], policy=policy,
                                              libraries_home=tmp_path / "lib",
                                              withhold_secrets={"OPT_TOKEN"})
    assert code == 0, err
    assert json.loads(out)["opt"] is None


def test_missing_procedure_names_the_available_ones(tmp_path):
    d = _routine(tmp_path)
    code, _out, err = procedures.run_procedure(
        d, "nope", [], policy=sandbox.SandboxPolicy(mode="off"),
        libraries_home=tmp_path / "lib")
    assert code == 2 and "probe" in err


def test_procedure_kind_is_gated_and_validated():
    assert "procedure" in GATED_KINDS
    assert not GrantPolicy().allows_kind("procedure")          # default OFF
    assert GrantPolicy(actions=frozenset({"procedure"})).allows_kind("procedure")
    assert validate_action({"say": "s", "kind": "procedure", "name": "probe"}) == []
    assert validate_action({"say": "s", "kind": "procedure"})  # name required


def test_procedure_action_end_to_end(make_routine, scripted, monkeypatch):
    """The engine path: capability on → the action reaches run_procedure with the
    routine dir + args, and the observation carries the procedure kind + output."""
    d = make_routine(slug="procr")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["capabilities"] = {"actions": ["procedure"]}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "procedures").mkdir()
    (d / "procedures" / "probe.py").write_text(SCRIPT, encoding="utf-8")
    calls = []
    monkeypatch.setattr("rsched.procedures.run_procedure",
                        lambda rd, name, args, **kw:
                        (calls.append((rd, name, list(args))) or (0, "ran", "")))
    scripted([
        {"say": "poll deterministically", "kind": "procedure", "name": "probe",
         "args": ["--json"]},
        finish(),
    ])
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "test-library"
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    events = read_events(run_dir / "transcript.jsonl")[0]
    obs = next(e for e in events if e["type"] == "observation"
               and e["payload"].get("kind") == "procedure")
    assert obs["payload"]["exit"] == 0 and obs["payload"]["stdout"] == "ran"
    assert calls == [(d, "probe", ["--json"])]
