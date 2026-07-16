"""The CLI command surface (cli.py cmd_*): exit codes, printed diagnostics, and the disk
effects each command promises — the renderer half lives in test_cli.py. Everything runs
hermetic: load_server_config is monkeypatched to tmp homes, LLM boundaries are canned.
"""

from types import SimpleNamespace

import pytest
import yaml

from rsched import cli
from rsched.config import load_server_config
from rsched.paths import atomic_write_json

REPO_SEED = cli.Path(__file__).resolve().parents[1] / "library-seed"


@pytest.fixture
def cli_server(tmp_path, monkeypatch):
    """Tmp homes wired into every cmd_* via the module's load_server_config seam."""
    import shutil

    lib = tmp_path / "library"
    for kind in ("workflows", "traits", "permissions"):
        shutil.copytree(REPO_SEED / kind, lib / kind)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": "t", "routines_home": str(tmp_path / "routines"),
        "libraries_home": str(lib)}), encoding="utf-8")
    server, problems = load_server_config(cfg_path)
    assert not problems
    (tmp_path / "routines").mkdir(exist_ok=True)
    monkeypatch.setattr(cli, "load_server_config", lambda: (server, []))
    return server


def _args(**kw):
    return SimpleNamespace(**kw)


# ---- validate ---------------------------------------------------------------------------


def test_validate_reports_ok_and_problems(cli_server, make_routine, capsys):
    make_routine(slug="good")
    broken = make_routine(slug="broken")
    raw = yaml.safe_load((broken / "routine.yaml").read_text(encoding="utf-8"))
    raw["description"] = ""
    raw["budgets"] = {"max_turns": 5, "made_up_budget": 1}
    (broken / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert cli.cmd_validate(_args(routine="good")) == 0
    out = capsys.readouterr().out
    assert "good: ok" in out

    assert cli.cmd_validate(_args(routine=None)) == 1      # sweeps every routine dir
    out = capsys.readouterr().out
    assert "broken: PROBLEMS" in out
    assert "made_up_budget" in out and "description is empty" in out
    assert "good: ok" in out                                # the healthy one still listed


# ---- abort ------------------------------------------------------------------------------


def test_abort_paths(cli_server, make_routine, capsys):
    d = make_routine(slug="abortee")

    # no active run → exit 1 with a human answer, not a stack trace
    assert cli.cmd_abort(_args(run_id="abortee")) == 1
    assert "no active run" in capsys.readouterr().err

    # active run with a dead pid → the abort is reported failed (exit 1), naming the run
    run_dir = d / "runs" / "20260716-200000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", {
        "run_id": "abortee:20260716-200000", "state": "running", "pid": 999_999_999,
        "started": "20260716-200000", "updated": "2026-07-16T20:00:00+00:00",
        "turn": 1, "usage": {}, "elapsed_s": 1})
    assert cli.cmd_abort(_args(run_id="abortee")) == 1
    assert "abortee:20260716-200000" in capsys.readouterr().err


# ---- lint -------------------------------------------------------------------------------


def test_lint_seed_clean_then_flags_junk(cli_server, capsys):
    assert cli.cmd_lint(_args(target=None)) == 0            # the shipped seeds must lint clean
    assert "PROBLEMS" not in capsys.readouterr().out

    junk = cli_server.libraries_home / "workflows" / "junk-flow.py"
    junk.write_text("print('not a workflow')\n", encoding="utf-8")
    assert cli.cmd_lint(_args(target=None)) == 1
    out = capsys.readouterr().out
    assert "junk-flow" in out and "PROBLEMS" in out

    assert cli.cmd_lint(_args(target="no-such-name")) == 0  # filter excludes the bad file
    junk.unlink()


# ---- suggest ----------------------------------------------------------------------------


def test_suggest_prints_ranking_and_none_fit(cli_server, capsys, monkeypatch):
    import rsched.workflows.suggest as sug

    monkeypatch.setattr(sug, "suggest", lambda server, instruction: {
        "suggestions": [{"slug": "general-task", "confidence": 0.9, "reason": "fits"},
                        {"slug": "converse", "confidence": 0.2, "reason": "chatty"}],
        "none_fit": True, "new_workflow_hint": "a bespoke scraping flow"})
    assert cli.cmd_suggest(_args(instruction="watch a feed")) == 0
    captured = capsys.readouterr()
    assert "0.90  general-task" in captured.out and "0.20  converse" in captured.out
    assert "a bespoke scraping flow" in captured.err


# ---- scaffold ---------------------------------------------------------------------------


def test_scaffold_creates_a_runnable_routine(cli_server, capsys):
    """No endpoints configured → decompose falls back to the verbatim pattern; the routine
    still lands complete: recipe, traits, config, tuning."""
    rc = cli.cmd_scaffold(_args(slug="scaffed", name="", workflow="general-task",
                                instruction_file=None, cron="0 7 * * *", tz="Europe/Berlin",
                                description="a scaffold test", tag=["t1", "t2"],
                                read_root=None, write_root=None))
    assert rc == 0
    d = cli_server.routines_home / "scaffed"
    assert (d / "main.md").exists() and (d / "routine.yaml").exists()
    assert (d / "tuning.yaml").exists()                     # deliberation rides every scaffold
    assert "Standing practices" in (d / "main.md").read_text(encoding="utf-8")

    rc = cli.cmd_scaffold(_args(slug="scaffed2", name="", workflow="no-such-pattern",
                                instruction_file=None, cron="", tz="Europe/Berlin",
                                description="", tag=None, read_root=None, write_root=None))
    assert rc == 2                                          # unknown pattern → error, no dir
    assert "no-such-pattern" in capsys.readouterr().err
    assert not (cli_server.routines_home / "scaffed2").exists()


# ---- run-once ---------------------------------------------------------------------------


def test_run_once_exit_codes_and_event_stream(cli_server, make_routine, scripted, capsys):
    from conftest import finish, write_file

    assert cli.cmd_run_once(_args(routine="missing", model=None, quiet=True)) == 2
    assert "missing routine.yaml" in capsys.readouterr().err

    make_routine(slug="oncer")
    scripted([write_file("state/probe.txt", content="x", say="Probing the state dir."),
              finish(status="ok", summary="all done")])
    rc = cli.cmd_run_once(_args(routine="oncer", model=None, quiet=False))
    assert rc == 0                                          # ok → exit 0
    captured = capsys.readouterr()
    assert "Probing the state dir." in captured.out         # the live event stream rendered
    assert "run dir:" in captured.err

    make_routine(slug="failer")
    scripted([finish(status="failed", summary="could not")])
    assert cli.cmd_run_once(_args(routine="failer", model=None, quiet=True)) == 1


# ---- main() dispatch --------------------------------------------------------------------


def test_main_parses_and_dispatches(cli_server, make_routine, capsys):
    make_routine(slug="viamain")
    assert cli.main(["validate", "viamain"]) == 0
    assert "viamain: ok" in capsys.readouterr().out
