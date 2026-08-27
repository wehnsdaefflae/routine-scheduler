"""Per-routine scripts — the routine's own persistent helper tooling. A
`scripts/<name>.py` PEP 723 script runs in the routine-workdir venv with the routine's
fs roots and ONLY the granted secrets its header declares as env (the util model); `gu`
is deliberately not on PATH — a step needing a util's capability belongs in the recipe.
"""

from __future__ import annotations

import json

import yaml

from conftest import finish
from rsched import sandbox, scripts
from rsched.config import ServerConfig
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.grantpolicy import GrantPolicy
from rsched.grants import GATED_KINDS

TS = "20260708-070000"

SCRIPT = '''# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""probe — env and args probe for the scripts tests.

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
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "probe.py").write_text(SCRIPT, encoding="utf-8")
    return d


def test_list_and_needs_from_own_header(tmp_path):
    d = _routine(tmp_path)
    have = scripts.list_scripts(d)
    assert [s["name"] for s in have] == ["probe"]
    assert "env and args probe" in have[0]["summary"]
    declared, net, optional = scripts.needs(d, "probe")
    assert declared == {"PROC_TOKEN", "OPT_TOKEN"}
    assert optional == {"OPT_TOKEN"} and net is False
    assert scripts.list_scripts(tmp_path / "empty") == []


def test_misdeclared_engine_keys_in_pep723_block(tmp_path):
    """F369 (R444/R419): `secrets = [...]` / `net = "outbound"` inside the PEP 723 block is
    a plausible author guess the engine never reads — needs() would silently yield no
    secrets and no network. misdeclared() names the misplaced keys so the script action can
    refuse loudly instead of the script failing obscurely at its first env read / socket."""
    d = _routine(tmp_path)
    assert scripts.misdeclared(d, "probe") == []          # docstring form: clean
    bad = SCRIPT.replace("# dependencies = []",
                         '# dependencies = ["requests"]\n'
                         '# secrets = ["FTP_SOURCES"]\n'
                         '# net = "outbound"')
    (d / "scripts" / "publish.py").write_text(bad, encoding="utf-8")
    assert scripts.misdeclared(d, "publish") == ["net", "secrets"]
    assert scripts.misdeclared(d, "gone") == []           # missing file: no crash

    from rsched.engine.executor import do_script

    class _Ctx:
        class routine:  # noqa: N801 — mirrors ctx.routine attribute shape
            dir = d
    obs = do_script({"kind": "script", "name": "publish"}, _Ctx)
    assert "error" in obs and "PEP 723" in obs["error"]
    assert "secrets: FTP_SOURCES" in obs["error"]         # the fix is taught, not implied


def test_run_script_declared_env_and_venv(tmp_path, monkeypatch):
    """The env is what the CALLER composed from the script's DECLARED grants; everything
    else in the store is scrubbed, and the util library is NOT reachable (no
    GLOBAL_UTILS_HOME / gu on PATH — a script is pure code, not a tool-user)."""
    d = _routine(tmp_path)
    monkeypatch.setattr("rsched.secrets.load_secrets",
                        lambda: {"OTHER_KEY": "not-granted"})
    monkeypatch.setenv("OTHER_KEY", "leaked-via-daemon-env")
    policy = sandbox.SandboxPolicy(mode="off")
    lib = tmp_path / "lib"
    code, out, err = scripts.run_script(
        d, "probe", ["a", "b"], policy=policy, libraries_home=lib,
        env_secrets={"PROC_TOKEN": "t-1", "OPT_TOKEN": "o-1"})
    assert code == 0, err
    data = json.loads(out)
    # declared secrets injected, the undeclared store key scrubbed, args pass through,
    # and no util-library handle reaches the child
    assert data == {"args": ["a", "b"], "token": "t-1", "opt": "o-1", "other": None,
                    "gu_home": None}
    # the run created a persistent venv in the routine's workdir (gitignored —
    # autocommit is `git add -A`) and executed with ITS python
    assert scripts.venv_python(d).exists()
    assert ".venv/" in (d / ".gitignore").read_text(encoding="utf-8")
    # a secret the caller did not pass is simply absent
    code, out, err = scripts.run_script(d, "probe", [], policy=policy,
                                        libraries_home=lib,
                                        env_secrets={"PROC_TOKEN": "t-2"})
    assert code == 0, err
    assert json.loads(out)["opt"] is None


def test_script_deps_parses_pep723(tmp_path):
    d = _routine(tmp_path)
    assert scripts.script_deps(d, "probe") == []
    withdeps = SCRIPT.replace("# dependencies = []",
                              '# dependencies = ["requests>=2", "lxml"]')
    (d / "scripts" / "fetcher.py").write_text(withdeps, encoding="utf-8")
    assert scripts.script_deps(d, "fetcher") == ["requests>=2", "lxml"]


def test_missing_script_names_the_available_ones(tmp_path):
    d = _routine(tmp_path)
    code, _out, err = scripts.run_script(
        d, "nope", [], policy=sandbox.SandboxPolicy(mode="off"),
        libraries_home=tmp_path / "lib")
    assert code == 2 and "probe" in err


def test_snake_case_script_name_is_reachable(tmp_path):
    """R336/R337: a run that authors scripts/gen_random_strings.py must be able to CALL
    it. The old kebab-only name check reported the very file the miss message told the
    model to write as nonexistent — while listing its stem as available — forever.
    """
    d = _routine(tmp_path)
    (d / "scripts" / "gen_random_strings.py").write_text(
        SCRIPT.replace("probe —", "gen_random_strings —"), encoding="utf-8")
    assert scripts.exists(d, "gen_random_strings")
    code, out, err = scripts.run_script(
        d, "gen_random_strings", ["x"], policy=sandbox.SandboxPolicy(mode="off"),
        libraries_home=tmp_path / "lib", env_secrets={})
    assert code == 0, err
    assert json.loads(out)["args"] == ["x"]
    # traversal-shaped or cased names stay invalid regardless of what is on disk
    assert not scripts.exists(d, "../evil")
    assert not scripts.exists(d, "Probe")
    assert not scripts.exists(d, "a.b")


def test_script_kind_is_gated_and_validated():
    assert "script" in GATED_KINDS
    assert not GrantPolicy().allows_kind("script")          # default OFF
    assert GrantPolicy(actions=frozenset({"script"})).allows_kind("script")
    assert validate_action({"say": "s", "kind": "script", "name": "probe"}) == []
    assert validate_action({"say": "s", "kind": "script"})  # name required


def test_script_action_end_to_end(make_routine, scripted, monkeypatch):
    """The engine path: capability on → run_script gets the routine dir, args, and an
    env filtered to the script's DECLARED names — granted in, undecided/denied/undeclared
    absent — and the observation carries the script kind + output."""
    d = make_routine(slug="scriptr")
    cfg = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    cfg["capabilities"] = {"actions": ["script"]}
    cfg["grants"] = {"secret:PROC_TOKEN": True, "secret:OPT_TOKEN": False,
                     "secret:GRANTED_UNDECLARED": True}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "scripts").mkdir()
    (d / "scripts" / "probe.py").write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setattr("rsched.secrets.load_secrets",
                        lambda: {"PROC_TOKEN": "g-1", "OPT_TOKEN": "d-1",
                                 "GRANTED_UNDECLARED": "u-1"})
    calls = []
    monkeypatch.setattr("rsched.scripts.run_script",
                        lambda rd, name, args, **kw:
                        (calls.append((rd, name, list(args), kw.get("env_secrets")))
                         or (0, "ran", "")))
    scripted([
        {"say": "poll deterministically", "kind": "script", "name": "probe",
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
               and e["payload"].get("kind") == "script")
    assert obs["payload"]["exit"] == 0 and obs["payload"]["stdout"] == "ran"
    rd, name, args, env_secrets = calls[0]
    assert (rd, name, args) == (d, "probe", ["--json"])
    assert env_secrets.get("PROC_TOKEN") == "g-1"           # declared + granted → in
    assert "OPT_TOKEN" not in env_secrets                   # declared + denied → absent
    assert "GRANTED_UNDECLARED" not in env_secrets          # granted but UNDECLARED → absent
