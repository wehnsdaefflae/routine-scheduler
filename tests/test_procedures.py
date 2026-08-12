"""D88: per-routine procedures under the operator's SYMMETRY rule — a routine is one
thing with two interpreters, and everything in its settings applies to both. A
`procedures/<name>.py` PEP 723 script runs in the routine-workdir venv with the
routine's fs roots, its GRANTED secrets/connections/machines as env, and the util
library on PATH.
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
                  "other": os.environ.get("OTHER_KEY"),
                  "gu_home": os.environ.get("GLOBAL_UTILS_HOME")}))
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


def test_run_procedure_settings_env_and_venv(tmp_path, monkeypatch):
    """Symmetry rule: the env is what the CALLER composed from the routine's settings
    (granted secrets + connections + machines); everything else in the store is
    scrubbed, and the util library is reachable (`gu` on PATH via GLOBAL_UTILS_HOME)."""
    d = _routine(tmp_path)
    monkeypatch.setattr("rsched.secrets.load_secrets",
                        lambda: {"OTHER_KEY": "not-granted"})
    monkeypatch.setenv("OTHER_KEY", "leaked-via-daemon-env")
    policy = sandbox.SandboxPolicy(mode="off")
    lib = tmp_path / "lib"
    code, out, err = procedures.run_procedure(
        d, "probe", ["a", "b"], policy=policy, libraries_home=lib,
        env_secrets={"PROC_TOKEN": "t-1", "OPT_TOKEN": "o-1"})
    assert code == 0, err
    data = json.loads(out)
    # the settings env injected, the not-granted store key scrubbed, args pass through
    assert data == {"args": ["a", "b"], "token": "t-1", "opt": "o-1", "other": None,
                    "gu_home": str(lib)}
    # the run created a persistent venv in the routine's workdir (gitignored —
    # autocommit is `git add -A`) and executed with ITS python
    assert procedures.venv_python(d).exists()
    assert ".venv/" in (d / ".gitignore").read_text(encoding="utf-8")
    # a secret the caller did not grant is simply absent
    code, out, err = procedures.run_procedure(d, "probe", [], policy=policy,
                                              libraries_home=lib,
                                              env_secrets={"PROC_TOKEN": "t-2"})
    assert code == 0, err
    assert json.loads(out)["opt"] is None


def test_script_deps_parses_pep723(tmp_path):
    d = _routine(tmp_path)
    assert procedures.script_deps(d, "probe") == []
    withdeps = SCRIPT.replace("# dependencies = []",
                              '# dependencies = ["requests>=2", "lxml"]')
    (d / "procedures" / "fetcher.py").write_text(withdeps, encoding="utf-8")
    assert procedures.script_deps(d, "fetcher") == ["requests>=2", "lxml"]


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
    """The engine path under the symmetry rule: capability on → run_procedure gets the
    routine dir, args, and an env composed from the routine's STANDING settings — every
    GRANTED store secret in, undecided/denied ones absent — and the observation carries
    the procedure kind + output."""
    d = make_routine(slug="procr")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["capabilities"] = {"actions": ["procedure"]}
    cfg["grants"] = {"secret:GRANTED_ONE": True, "secret:DENIED_ONE": False}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "procedures").mkdir()
    (d / "procedures" / "probe.py").write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setattr("rsched.secrets.load_secrets",
                        lambda: {"GRANTED_ONE": "g-1", "DENIED_ONE": "d-1",
                                 "UNDECIDED_ONE": "u-1"})
    calls = []
    monkeypatch.setattr("rsched.procedures.run_procedure",
                        lambda rd, name, args, **kw:
                        (calls.append((rd, name, list(args), kw.get("env_secrets")))
                         or (0, "ran", "")))
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
    rd, name, args, env_secrets = calls[0]
    assert (rd, name, args) == (d, "probe", ["--json"])
    assert env_secrets.get("GRANTED_ONE") == "g-1"          # granted → in the env
    assert "DENIED_ONE" not in env_secrets                  # denied → absent
    assert "UNDECIDED_ONE" not in env_secrets               # undecided → absent
