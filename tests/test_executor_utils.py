"""The util execution seam (executor.do_util → utils_run.run_util): a REAL `uv run` of a
tiny script, and the failure contract — a broken call must return its usage line plus the
grants-aware repair route (fix-it-yourself vs escalate), never a silent dead end.
"""

import pytest

from rsched.config import ServerConfig, load_routine
from rsched.engine.budgets_config import Budgets
from rsched.engine.executor import dispatch
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript
from rsched.grantpolicy import GrantPolicy

ECHOER = '''"""echoer — prints its arguments back.

usage: gu echoer <words…>
tags: test
"""
import sys

print("echo:", " ".join(sys.argv[1:]))
'''

FLOODER = '''"""flooder — prints more than one observation's worth of stdout.

usage: gu flooder
tags: test
"""
print("F" * 20_000)
'''

CRASHER = '''"""crasher — always exits 3 with a diagnostic.

usage: gu crasher --right-flag
tags: test
"""
import sys

print("boom diagnostics", file=sys.stderr)
sys.exit(3)
'''


@pytest.fixture
def util_ctx(make_routine, tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path

    # run_util shells out via `uv` — put its usual home on PATH (non-login shells lack it)
    if not shutil.which("uv"):
        local_bin = Path.home() / ".local" / "bin"
        if not (local_bin / "uv").exists():
            pytest.skip("uv not available — run_util requires it")
        monkeypatch.setenv("PATH", f"{local_bin}:{os.environ.get('PATH', '')}")
    home = tmp_path / "libraries"
    for name, body in (("echoer", ECHOER), ("crasher", CRASHER), ("flooder", FLOODER)):
        d = home / "utils" / name          # utils live in the library's utils/ subtree
        d.mkdir(parents=True)
        (d / "main.py").write_text(body, encoding="utf-8")
    routine = make_routine(slug="utiler")
    cfg, _problems = load_routine(routine)
    run_dir = routine / "runs" / "20260716-210000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = home
    return RunContext(routine=cfg, server=server, registry=None, run_ts="20260716-210000",
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


def test_util_runs_for_real_and_captures_stdout(util_ctx):
    obs = dispatch({"kind": "util", "name": "echoer", "args": ["hello", "world"]}, util_ctx)
    assert obs["exit"] == 0
    assert "echo: hello world" in obs["stdout"]


def test_failed_util_teaches_usage_and_the_authoring_repair_route(util_ctx):
    util_ctx.grants = GrantPolicy(actions=frozenset({"write_util"}))
    obs = dispatch({"kind": "util", "name": "crasher", "args": ["--wrong"]}, util_ctx)
    assert obs["exit"] == 3
    assert "boom diagnostics" in obs["stderr"]              # the repair material survives
    assert obs["usage"] == "usage: gu crasher --right-flag"  # the correct call, from the header
    assert "write_util the corrected script" in obs["hint"]  # authoring granted → fix in place


def test_failed_util_without_authoring_escalates_instead(util_ctx):
    util_ctx.grants = GrantPolicy()                          # no write_util capability
    obs = dispatch({"kind": "util", "name": "crasher", "args": []}, util_ctx)
    assert obs["exit"] == 3
    assert "cannot revise utils itself" in obs["hint"]       # escalate via deferred ask_user
    assert "ask_user" in obs["hint"]


def test_output_too_large_for_the_observation_is_spilled_not_lost(util_ctx):
    """The transcript records the TRUNCATED observation, so without the spill store the
    band between the capture cap (1 MB) and the observation cap has no survivor — and
    re-running is not the same data for a fetch, a paid call, or a mailbox read."""
    from rsched.engine.observations import OBS_CAP_CHARS

    util_ctx.turn = 4
    obs = dispatch({"kind": "util", "name": "flooder", "args": []}, util_ctx)
    assert obs["truncated"] is True
    assert len(obs["stdout"]) <= OBS_CAP_CHARS + 200        # the observation stays capped…
    rel = obs["full_output"]["stdout"]
    assert "t4-flooder.out" in rel                          # stamped with the turn that got it
    saved = (util_ctx.routine.dir / rel).read_text(encoding="utf-8")
    assert saved.count("F") == 20_000                       # …the whole output is on disk
    # an ordinary-sized output is already in the transcript verbatim — nothing is copied
    small = dispatch({"kind": "util", "name": "echoer", "args": ["hi"]}, util_ctx)
    assert "full_output" not in small


def test_util_show_and_missing_answer_with_the_catalog(util_ctx):
    obs = dispatch({"kind": "util", "name": "show", "args": ["echoer"]}, util_ctx)
    assert "prints its arguments back" in obs["source"]
    missing = dispatch({"kind": "util", "name": "show", "args": ["nope"]}, util_ctx)
    assert missing["missing"] is True and "echoer" in missing["available"]
    gone = dispatch({"kind": "util", "name": "unknown-util", "args": []}, util_ctx)
    assert gone["missing"] is True
    assert set(gone["available"]) == {"crasher", "echoer", "flooder"}


def test_util_miss_names_a_matching_routine_local_script(util_ctx):
    # F330/R367: `util name=X` where X is a routine-local script must point at the script
    # action instead of dead-ending on the global catalog — the reporter was told scripts/
    # is the place for private helpers, then had no path from this miss to running one.
    from rsched.engine.observations import format_observation

    sdir = util_ctx.routine.dir / "scripts"
    sdir.mkdir()
    (sdir / "explode.py").write_text('"""explode — test helper."""\n', encoding="utf-8")
    obs = dispatch({"kind": "util", "name": "explode"}, util_ctx)
    assert obs["missing"] is True and obs["script_match"] is True
    text = format_observation(obs)
    assert "ROUTINE-LOCAL script" in text and "action:script" in text
    # a plain miss (no matching script) stays hint-free
    plain = dispatch({"kind": "util", "name": "nope"}, util_ctx)
    assert "script_match" not in plain
    assert "ROUTINE-LOCAL" not in format_observation(plain)


def test_util_search_ranks_by_keyword_and_keeps_the_catalog_floor(util_ctx):
    """D52 Phase 3: `util name=search` is a discovery verb — keyword-rank the live catalog,
    return only close matches, and ALWAYS name the always-on floor so a miss hides nothing."""
    from rsched.engine.observations import format_observation

    obs = dispatch({"kind": "util", "name": "search", "args": ["arguments"]}, util_ctx)
    assert obs["name"] == "search" and obs["query"] == "arguments"
    # echoer's summary is "prints its arguments back" — it must rank into the hits…
    assert "echoer" in obs["listing"]
    # …and the retrieval-miss floor is always present
    assert "CAPABILITIES" in obs["listing"]
    # the discovery result is a NON-executing call: no util run, so no reliability counter
    assert "exit" not in obs
    # the observation renders under its own query-labelled header, not "util list"
    rendered = format_observation(obs)
    assert "util search 'arguments'" in rendered

    # a query nothing matches still returns the floor, not a dead end
    miss = dispatch({"kind": "util", "name": "search", "args": ["zzzznomatch"]}, util_ctx)
    assert "No util" in miss["listing"] and "CAPABILITIES" in miss["listing"]

    # an empty query teaches the shape instead of dumping everything
    empty = dispatch({"kind": "util", "name": "search", "args": []}, util_ctx)
    assert empty["query"] == "" and "needs keywords" in empty["listing"]


def test_util_show_full_and_range_page_the_whole_source(util_ctx):
    """D42-A: a >24k util must be COMPLETELY readable without shell — the capped default
    teaches --full/--range, --full returns everything, --range pages by 1-based lines."""
    body = ('"""big — test filler.\n\nusage: gu big\ntags: test\n"""\n'
            + "\n".join(f"# line {i}" for i in range(4000)))
    d = util_ctx.server.libraries_home / "utils" / "big"
    d.mkdir(parents=True)
    (d / "main.py").write_text(body, encoding="utf-8")
    capped = dispatch({"kind": "util", "name": "show", "args": ["big"]}, util_ctx)
    assert capped["truncated"] is True and "--full" in capped["hint"]
    full = dispatch({"kind": "util", "name": "show", "args": ["big", "--full"]}, util_ctx)
    assert full["truncated"] is False and full["source"] == body
    window = dispatch({"kind": "util", "name": "show",
                       "args": ["big", "--range", "6", "8"]}, util_ctx)
    assert window["source"].splitlines()[0] == "[lines 6-8 of 4005]"
    assert window["source"].splitlines()[1:] == ["# line 0", "# line 1", "# line 2"]
    assert window["truncated"] is True
    bad = dispatch({"kind": "util", "name": "show", "args": ["big", "--range", "x"]}, util_ctx)
    assert "[bad --range]" in bad["source"]
