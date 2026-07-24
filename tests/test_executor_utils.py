"""The util execution seam (executor.do_util → utils_lib.run_util): a REAL `uv run` of a
tiny script, and the failure contract — a broken call must return its usage line plus the
grants-aware repair route (fix-it-yourself vs escalate), never a silent dead end.
"""

import pytest

from rsched.config import ServerConfig, load_routine
from rsched.engine.executor import dispatch
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript
from rsched.grants import GrantPolicy

ECHOER = '''"""echoer — prints its arguments back.

usage: gu echoer <words…>
tags: test
"""
import sys

print("echo:", " ".join(sys.argv[1:]))
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
    for name, body in (("echoer", ECHOER), ("crasher", CRASHER)):
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


def test_util_show_and_missing_answer_with_the_catalog(util_ctx):
    obs = dispatch({"kind": "util", "name": "show", "args": ["echoer"]}, util_ctx)
    assert "prints its arguments back" in obs["source"]
    missing = dispatch({"kind": "util", "name": "show", "args": ["nope"]}, util_ctx)
    assert missing["missing"] is True and "echoer" in missing["available"]
    gone = dispatch({"kind": "util", "name": "unknown-util", "args": []}, util_ctx)
    assert gone["missing"] is True and set(gone["available"]) == {"crasher", "echoer"}


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
