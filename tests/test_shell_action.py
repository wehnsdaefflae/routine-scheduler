"""The `shell` ACTION KIND (0.287.0) — what the move off the reserved util bought, and what
it must not have cost.

Two contracts are pinned here and they pull in opposite directions:

- GATING got stronger. `capabilities.utils` is an exception list, so the old reserved util was
  only ever gated because a permission doc happened to name it; a gated KIND is projected out
  of the schema a run is sent, so a routine without the capability cannot even generate the
  call. Both halves are asserted: the denial and the ungeneratability.
- The SANDBOX must be unchanged. The retired util declared `fs: roots` + `net: outbound` — the
  widest terms available — so its jail was exactly the run's granted roots, and it received no
  store secret because it declared none. `run_shell` reproduces all three. A regression here
  would turn a gating improvement into a sandbox hole, so the jail inputs are pinned directly.
"""

import jsonschema
import pytest

from rsched import shellrun
from rsched.config import ServerConfig, load_routine
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.budgets_config import Budgets
from rsched.engine.executor import dispatch
from rsched.engine.kindsurface import effective_kinds, schema_for_kinds
from rsched.engine.observations import format_observation
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript
from rsched.grantpolicy import GrantPolicy
from rsched.grants import GATED_KINDS


@pytest.fixture
def shell_ctx(make_routine, tmp_path):
    routine = make_routine(slug="sheller")
    cfg, _problems = load_routine(routine)
    run_dir = routine / "runs" / "20260903-120000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    (server.libraries_home / "utils").mkdir(parents=True)
    server.sandbox = "off"      # the jail's INPUTS are pinned separately; this exercises exec
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts="20260903-120000",
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.grants = GrantPolicy(actions=frozenset({"shell"}))
    return ctx


# -- the contract -----------------------------------------------------------------------

def test_shell_is_a_gated_kind_with_the_shell_permission_as_its_source():
    from rsched.grants import _DEFAULT_KIND_SOURCE
    assert "shell" in GATED_KINDS
    assert _DEFAULT_KIND_SOURCE["shell"] == "shell"


def test_schema_accepts_the_command_and_its_two_options():
    v = jsonschema.Draft202012Validator(ACTION_SCHEMA)
    action = {"say": "s", "kind": "shell", "command": "ls -la | head",
              "timeout_s": 900, "path": "state"}
    v.validate(action)
    assert validate_action(action) == []
    assert validate_action({"say": "s", "kind": "shell"}) == [
        "kind=shell requires a non-empty 'command' field"]


def test_the_kind_is_ungeneratable_without_the_capability():
    """The reason for the move: a routine without the capability is not sent the kind at all,
    so a denied call is never generated rather than generated and then rejected."""
    without = GrantPolicy()
    assert "shell" not in effective_kinds(None, without)
    projected = schema_for_kinds(effective_kinds(None, without))
    assert "shell" not in projected["properties"]["kind"]["enum"]
    assert "command" not in projected["properties"]
    # and the validator refuses it too, with a denial naming the covering permission
    denial = without.deny({"kind": "shell", "command": "rm -rf /"})
    assert "kind=shell is switched OFF" in denial
    assert "shell permission" in denial


def test_the_capability_switches_it_on_end_to_end():
    with_shell = GrantPolicy(actions=frozenset({"shell"}))
    assert "shell" in effective_kinds(None, with_shell)
    assert with_shell.deny({"kind": "shell", "command": "true"}) is None
    assert validate_action({"say": "s", "kind": "shell", "command": "true"},
                           allowed_kinds={"shell"}, grants=with_shell) == []


def test_the_permission_doc_requires_the_action_not_the_util():
    """The seed doc is what a fresh instance gets AND what the migration copies over the live
    one; a `requires: utils:` here would reserve a util that no longer exists."""
    from pathlib import Path

    from rsched.grants import normalize_capabilities
    from rsched.library_docs import parse_lenient

    doc = (Path(__file__).resolve().parents[1] / "library-seed" / "permissions" / "shell.md")
    meta, body = parse_lenient(doc.read_text(encoding="utf-8"))
    req, problems = normalize_capabilities(meta.get("requires"), label="requires", requires=True)
    assert problems == []
    assert req == {"actions": ["shell"]}
    assert "util" not in body.split("ACTION")[0]     # the prose leads with the action, not a util


# -- the effect -------------------------------------------------------------------------

def test_command_runs_and_captures_both_streams(shell_ctx):
    obs = dispatch({"kind": "shell", "command": "printf 'a\\nb\\n' | wc -l; echo oops >&2"},
                   shell_ctx)
    assert obs["exit"] == 0
    assert obs["stdout"].strip() == "2"
    assert "oops" in obs["stderr"]
    assert "OBSERVATION (shell, exit 0)" in format_observation(obs)


def test_a_failing_command_reports_its_exit_and_adds_no_advice(shell_ctx):
    """`grep -q`, `test -f` and a suite the run is iterating on all exit non-zero as their
    ANSWER, so a "you should have written a util" tail on every failure would be noise on the
    normal path. The promotion conduct lives in the kind's bullet and the permission body."""
    obs = dispatch({"kind": "shell", "command": "exit 3"}, shell_ctx)
    assert obs["exit"] == 3
    assert "hint" not in obs
    assert format_observation(obs).startswith("OBSERVATION (shell, exit 3):")


def test_timeout_kills_the_process_group_and_reports_124(shell_ctx):
    obs = dispatch({"kind": "shell", "command": "sleep 30", "timeout_s": 1}, shell_ctx)
    assert obs["exit"] == shellrun.TIMEOUT_EXIT == 124
    assert obs["timed_out"] is True
    assert "timed out after 1s" in obs["stderr"]


def test_cwd_defaults_to_the_routine_dir_and_path_moves_it(shell_ctx):
    (shell_ctx.routine.dir / "state" / "marker.txt").write_text("x", encoding="utf-8")
    here = dispatch({"kind": "shell", "command": "pwd"}, shell_ctx)
    assert here["stdout"].strip() == str(shell_ctx.routine.dir)
    assert "cwd" not in here                       # the default is not worth an observation line
    there = dispatch({"kind": "shell", "command": "ls", "path": "state"}, shell_ctx)
    assert "marker.txt" in there["stdout"]
    assert there["cwd"] == str(shell_ctx.routine.dir / "state")
    assert "in " + there["cwd"] in format_observation(there)


def test_no_store_secret_reaches_the_command(shell_ctx, monkeypatch):
    """The retired util declared no `secrets:` header, so declared-only injection handed it
    nothing and scrubbed the daemon's own copy. Parity is the whole safety claim of the move."""
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"SHELL_TEST_TOKEN": "s3cret"})
    monkeypatch.setenv("SHELL_TEST_TOKEN", "s3cret")
    obs = dispatch({"kind": "shell", "command": 'echo "[${SHELL_TEST_TOKEN:-absent}]"'},
                   shell_ctx)
    assert obs["stdout"].strip() == "[absent]"


def test_output_is_capped_per_stream_and_spilled_in_full(shell_ctx):
    """64 KB per stream, head+tail — the retired util's cap, kept — and the band between that
    and the (much smaller) observation cap is saved to `.util_outputs/` like a util's."""
    from rsched.engine.observations import OBS_CAP_CHARS

    shell_ctx.turn = 7
    obs = dispatch({"kind": "shell",
                    "command": "python3 -c \"print('F' * 200_000)\""}, shell_ctx)
    assert obs["truncated"] is True
    assert len(obs["stdout"]) <= OBS_CAP_CHARS + 200         # the observation stays small
    rel = obs["full_output"]["stdout"]
    assert "t7-shell.out" in rel
    saved = (shell_ctx.routine.dir / rel).read_text(encoding="utf-8")
    assert len(saved) <= shellrun.STREAM_CAP + 200           # …and the spill carries the cap
    assert "head+tail kept" in saved
    assert "[full output]" in format_observation(obs)


def test_an_empty_command_is_refused_before_a_process_exists(shell_ctx):
    obs = dispatch({"kind": "shell", "command": "   "}, shell_ctx)
    assert obs["exit"] == 2
    assert obs["stderr"] == "empty command"


# -- the sandbox (the thing that must NOT have changed) ----------------------------------

def test_the_jail_is_composed_on_the_retired_utils_own_terms(shell_ctx, monkeypatch):
    """`fs: roots` + `net: outbound` were the util's declarations, so its intersection term
    was a no-op and its effective bound was the run's granted roots. Anything narrower here
    would break existing routines; anything wider would make the move a sandbox regression."""
    seen = {}

    def fake_wrap(cmd, *, policy, libraries_home, net, fs_roots, fs_paths):
        seen.update(policy=policy, net=net, fs_roots=fs_roots, fs_paths=fs_paths, cmd=cmd)
        return list(cmd)

    monkeypatch.setattr("rsched.sandbox.wrap", fake_wrap)
    shell_ctx.server.sandbox = "permissive"
    dispatch({"kind": "shell", "command": "true"}, shell_ctx)
    assert seen["net"] is True and seen["fs_roots"] is True and seen["fs_paths"] == ()
    assert seen["cmd"][:2] == ["bash", "-c"]
    # the policy is the RUN's, exactly as `util` builds it — not a fresh, wider one
    assert seen["policy"].own_dir == shell_ctx.routine.dir
    assert seen["policy"].mode == "permissive"


def test_a_strict_mode_refusal_becomes_an_observation_not_a_crash(shell_ctx, monkeypatch):
    from rsched import sandbox

    def refuse(*_a, **_k):
        raise sandbox.SandboxRefusal("the jail cannot engage")

    monkeypatch.setattr("rsched.sandbox.wrap", refuse)
    obs = dispatch({"kind": "shell", "command": "true"}, shell_ctx)
    assert obs["exit"] == 2
    assert "the jail cannot engage" in obs["stderr"]
