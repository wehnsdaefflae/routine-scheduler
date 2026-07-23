"""Util library: real uv-run of utils (catalog, run, selftest, composition). Uses uv.

Real runs use OFF/permissive sandbox policies: OFF where the test targets observation
shaping or catalog logic, PERMISSIVE where the whole run chain (uv, the gu dispatcher)
should pass through the real jail when the kernel offers one (test_landlock.py owns the
enforcement assertions themselves).
"""

import shutil

import pytest

from rsched import sandbox, utils_lib

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv required to run utils")

OFF = sandbox.SandboxPolicy(mode="off")
PERMISSIVE = sandbox.SandboxPolicy(mode="permissive")

ADDER = '''# /// script
# dependencies = []
# ///
"""adder — add two integers. usage: gu adder A B [--json]"""
import argparse, json, sys

def run(a, b):
    return a + b

def selftest():
    assert run(2, 3) == 5, "adder broken"
    print("selftest: ok", file=sys.stderr)
    return 0

def main():
    p = argparse.ArgumentParser(prog="gu adder")
    p.add_argument("a", nargs="?", type=int); p.add_argument("b", nargs="?", type=int)
    p.add_argument("--json", action="store_true"); p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    result = run(args.a, args.b)
    print(json.dumps({"sum": result}) if args.json else result)
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''

# composes with adder via the `gu` dispatcher (utils_home is on PATH when a util runs)
DOUBLER = '''# /// script
# dependencies = []
# ///
"""doubler — double N by calling the adder util. usage: gu doubler N [--json]"""
import argparse, json, subprocess, sys

def run(n):
    out = subprocess.run(["gu", "adder", str(n), str(n), "--json"], capture_output=True, text=True)
    return json.loads(out.stdout)["sum"]

def selftest():
    assert run(4) == 8, "doubler broken"
    print("selftest: ok", file=sys.stderr)
    return 0

def main():
    p = argparse.ArgumentParser(prog="gu doubler")
    p.add_argument("n", nargs="?", type=int)
    p.add_argument("--json", action="store_true"); p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    r = run(args.n)
    print(json.dumps({"double": r}) if args.json else r)
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def test_ensure_library_and_dispatcher(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    assert (home / "gu").exists() and (home / ".git").is_dir()
    assert utils_lib.list_utils(home) == []
    assert "no global utils yet" in utils_lib.catalog_text(home)


def test_write_run_selftest_and_catalog(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "adder", ADDER)

    ok, out = utils_lib.selftest(home, "adder", policy=OFF)
    assert ok, out
    code, stdout, _ = utils_lib.run_util(home, "adder", ["2", "3", "--json"], policy=OFF)
    assert code == 0 and '"sum": 5' in stdout
    catalog = utils_lib.catalog_text(home)
    assert "adder — add two integers." in catalog


def test_util_composition(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "adder", ADDER)
    utils_lib.write_util_file(home, "doubler", DOUBLER)
    # PERMISSIVE: on a Landlock kernel this whole chain (uv → doubler → gu → adder) runs
    # inside the real jail — the composition test doubles as the sandbox-compat canary.
    ok, out = utils_lib.selftest(home, "doubler", policy=PERMISSIVE)
    assert ok, out
    code, stdout, _ = utils_lib.run_util(home, "doubler", ["4", "--json"], policy=PERMISSIVE)
    assert code == 0 and '"double": 8' in stdout


def test_run_missing_and_invalid(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    code, _, err = utils_lib.run_util(home, "ghost", [], policy=OFF)
    assert code == 2 and "no util named" in err
    code, _, err = utils_lib.run_util(home, "Bad Name", [], policy=OFF)
    assert code == 2 and "invalid util name" in err


def test_catalog_text_includes_usage_and_call_shape(tmp_path):
    from rsched.utils_lib import catalog_text
    d = tmp_path / "utils" / "demo"
    d.mkdir(parents=True)
    (d / "main.py").write_text('"""demo — does a demo thing.\n\n'
                               'usage: gu demo TARGET [--json]\n\nsecrets: (none)\n"""\n')
    text = catalog_text(tmp_path)
    assert "demo — does a demo thing." in text
    assert "usage: gu demo TARGET [--json]" in text
    assert '"args": ["<arg>", "--flag"]' in text          # the call shape

def test_failed_util_observation_teaches_usage(tmp_path):

    from rsched.engine.observations import format_observation
    obs = {"kind": "util", "name": "demo", "args": [], "exit": 2,
           "stdout": "", "stderr": "usage error",
           "usage": "usage: gu demo TARGET [--json]",
           "hint": "pass every argument in `args` as a JSON array of strings"}
    text = format_observation(obs)
    assert "[usage] usage: gu demo TARGET" in text and "[hint]" in text


def _ctx(home, grants=None):
    from types import SimpleNamespace
    return SimpleNamespace(server=SimpleNamespace(libraries_home=home, sandbox="off"),
                           routine=SimpleNamespace(dir=home, fs_read_roots=[],
                                                   fs_write_roots=[], connections={},
                                                   machines=[]),
                           grants=grants,
                           count_util=lambda *a, **k: None)


def test_util_show_returns_source(tmp_path):
    """`util show <name>` is write_util's read counterpart — repair needs the source."""
    from rsched.engine.executor import do_util
    from rsched.engine.observations import format_observation

    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "demo", '"""demo — demo.\n\nusage: gu demo\n"""\nX = 1\n')
    obs = do_util({"kind": "util", "name": "show", "args": ["demo"]}, _ctx(tmp_path))
    assert obs["target"] == "demo" and "X = 1" in obs["source"]
    text = format_observation(obs)
    assert "write_util the COMPLETE corrected script" in text and "X = 1" in text
    # unknown / traversal-shaped targets → a missing observation, never a file read
    for bad in ("ghost", "../secrets", ""):
        obs = do_util({"kind": "util", "name": "show", "args": [bad]}, _ctx(tmp_path))
        assert obs.get("missing") is True, bad
        assert "demo" in obs["available"]


FAILING_UTIL = '''# /// script
# dependencies = []
# ///
"""boomer — always fails (test fixture).

usage: gu boomer
"""
import sys
print("start-of-trace " + "x" * 12000 + " end-of-trace", file=sys.stderr)
sys.exit(3)
'''


def test_failed_util_teaches_repair_and_keeps_trace_tail(tmp_path):
    """A nonzero exit carries the repair path (show → fix → write_util, or a deferred
    ask_user for environmental walls) and preserves the traceback's END through truncation."""
    from rsched.engine.executor import do_util

    utils_lib.ensure_library(tmp_path)
    utils_lib.write_util_file(tmp_path, "boomer", FAILING_UTIL)
    obs = do_util({"kind": "util", "name": "boomer", "args": []}, _ctx(tmp_path))
    assert obs["exit"] == 3
    assert '"show"' in obs["hint"] and "write_util" in obs["hint"] and "ask_user" in obs["hint"]
    assert "end-of-trace" in obs["stderr"]        # the tail survives truncation
    assert len(obs["stderr"]) < 12000             # …but the whole flood does not
    # without a write_util grant the hint routes to escalation, not self-repair
    from rsched.grants import GrantPolicy

    obs2 = do_util({"kind": "util", "name": "boomer", "args": []}, _ctx(tmp_path, GrantPolicy()))
    assert "cannot revise utils" in obs2["hint"] and "corrected script" not in obs2["hint"]


GOOD_UTIL = (
    "# /// script\n# dependencies = []\n# ///\n"
    '"""demo — a demo util.\n\n'
    "usage: gu demo [--json]\n"
    "calls: (none)\n"
    "secrets: DEMO_API_KEY\n"
    "tags: demo, testing\n"
    "net: none\n"
    '"""\n'
    'import os\nkey = os.environ.get("DEMO_API_KEY")\n'
)


def test_header_problems_gate():
    """The util doc standard is enforced: tags required, every credential-looking env
    var the code reads must be DECLARED in the docstring's secrets: line (a comment-form
    `# secrets:` above the docstring is invisible — the deepgram failure mode), and a
    net: declaration is required (the sandbox keys off it)."""
    from rsched.utils_lib import header_problems

    assert header_problems(GOOD_UTIL) == []
    no_tags = GOOD_UTIL.replace("tags: demo, testing\n", "")
    assert any("tags" in p for p in header_problems(no_tags))
    undeclared = GOOD_UTIL.replace("secrets: DEMO_API_KEY\n", "secrets: (none)\n")
    probs = header_problems(undeclared)
    assert any("DEMO_API_KEY" in p for p in probs)
    # comment-form declaration outside the docstring does NOT count
    comment_form = ("# secrets: DEMO_API_KEY\n"
                    + GOOD_UTIL.replace("secrets: DEMO_API_KEY\n", ""))
    assert any("DEMO_API_KEY" in p for p in header_problems(comment_form))
    # plain env vars (no KEY/TOKEN/SECRET shape) need no declaration
    plain = GOOD_UTIL.replace('os.environ.get("DEMO_API_KEY")', 'os.environ.get("HOME")')
    assert header_problems(plain) == []
    # the net: line is required, with exactly outbound|none as values
    no_net = GOOD_UTIL.replace("net: none\n", "")
    assert any("net:" in p for p in header_problems(no_net))
    bad_net = GOOD_UTIL.replace("net: none\n", "net: sometimes\n")
    assert any("net:" in p for p in header_problems(bad_net))
    assert header_problems(GOOD_UTIL.replace("net: none\n", "net: outbound\n")) == []
    # grouped read (the `ftp` pattern): names in a tuple looped over os.environ — the
    # credential-shaped one must be declared even though it never appears at an environ[...] site.
    grouped = (
        "# /// script\n# dependencies = []\n# ///\n"
        '"""grp — grouped-read util.\n\n'
        "usage: gu grp\ncalls: (none)\ntags: test\nnet: outbound\n"
        '"""\n'
        "import os\n"
        '_KEYS = ("FTP_HOST", "FTP_USER", "FTP_PASS", "FTP_PORT")\n'
        "cfg = {k: os.environ.get(k) for k in _KEYS}\n"
    )
    assert any("FTP_PASS" in p for p in header_problems(grouped)), header_problems(grouped)
    declared_grp = grouped.replace("tags: test\n", "secrets: FTP_PASS\ntags: test\n")
    assert header_problems(declared_grp) == []


def test_parse_header_net_and_calls():
    h = utils_lib.parse_header(GOOD_UTIL)
    assert h["net"] == "none" and h["calls"] == [] and h["secrets"] == ["DEMO_API_KEY"]
    with_calls = GOOD_UTIL.replace("calls: (none)\n", "calls: adder, page-fetch\n")
    assert utils_lib.parse_header(with_calls)["calls"] == ["adder", "page-fetch"]
    # a header without the lines parses as undeclared — fail closed downstream
    bare = '"""x — y.\n\nusage: gu x\n"""\n'
    h = utils_lib.parse_header(bare)
    assert h["net"] == "" and h["calls"] == [] and h["secrets"] == []


def _write_header_util(home, name, *, secrets="(none)", net="none", calls="(none)"):
    utils_lib.write_util_file(home, name, (
        "# /// script\n# dependencies = []\n# ///\n"
        f'"""{name} — fixture.\n\nusage: gu {name}\ncalls: {calls}\n'
        f"secrets: {secrets}\ntags: t\nnet: {net}\n"
        '"""\nprint("ok")\n'
    ))


def test_util_needs_transitive_closure(tmp_path):
    """Secrets and network resolve across the docstring `calls:` graph: the whole call
    tree runs inside ONE jail and ONE env, so a caller inherits its callees' needs."""
    utils_lib.ensure_library(tmp_path)
    _write_header_util(tmp_path, "leaf", secrets="LEAF_TOKEN", net="outbound")
    _write_header_util(tmp_path, "middle", calls="leaf")
    _write_header_util(tmp_path, "top", calls="middle", secrets="TOP_KEY")
    _write_header_util(tmp_path, "loner")
    secrets, net = utils_lib.util_needs(tmp_path, "top")
    assert secrets == {"TOP_KEY", "LEAF_TOKEN"} and net is True
    secrets, net = utils_lib.util_needs(tmp_path, "loner")
    assert secrets == set() and net is False
    # cycles terminate; a missing callee contributes nothing
    _write_header_util(tmp_path, "a", calls="b")
    _write_header_util(tmp_path, "b", calls="a, ghost")
    assert utils_lib.util_needs(tmp_path, "a") == (set(), False)


def test_child_env_scopes_secrets(tmp_path, monkeypatch):
    """The central store injects ONLY declared secrets; undeclared store keys are scrubbed
    even out of the inherited daemon environment; STRIP_VARS go unconditionally."""
    utils_lib.ensure_library(tmp_path)
    _write_header_util(tmp_path, "scoped", secrets="MY_TOKEN")
    monkeypatch.setattr("rsched.secrets.load_secrets",
                        lambda: {"MY_TOKEN": "t-1", "OTHER_KEY": "o-1"})
    monkeypatch.setenv("OTHER_KEY", "leaked-via-daemon-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never")
    monkeypatch.setenv("UNRELATED", "stays")
    env = utils_lib._child_env(tmp_path, "scoped")
    assert env["MY_TOKEN"] == "t-1"
    assert "OTHER_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["UNRELATED"] == "stays"


def test_was_deleted_reads_git_history(tmp_path):
    """The never-recreate rule's probe: a deletion anywhere in the library's history
    counts; a slug that never existed (or a fresh recreate) reads as expected."""
    utils_lib.ensure_library(tmp_path)
    _write_header_util(tmp_path, "doomed")
    utils_lib.git_commit(tmp_path, "create doomed")
    assert utils_lib.was_deleted(tmp_path, "doomed") is False
    assert utils_lib.was_deleted(tmp_path, "never-existed") is False
    shutil.rmtree(utils_lib.util_dir(tmp_path, "doomed"))
    utils_lib.git_commit(tmp_path, "delete util doomed via web")
    assert utils_lib.was_deleted(tmp_path, "doomed") is True
    # recreating does not erase the history — the guard still consults the user
    _write_header_util(tmp_path, "doomed")
    utils_lib.git_commit(tmp_path, "recreate doomed")
    assert utils_lib.was_deleted(tmp_path, "doomed") is True


def test_write_and_remove_reject_non_slug_names(tmp_path):
    """Backstop under validate_action's slug gate: a traversal-shaped name must never
    resolve to a path outside utils/."""
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    with pytest.raises(ValueError, match="invalid util name"):
        utils_lib.write_util_file(home, "../../evil", "# x")
    with pytest.raises(ValueError, match="invalid util name"):
        utils_lib.remove_util_file(home, "../adder")


def test_dispatcher_list_skips_non_util_entries(tmp_path):
    """`gu list` shows only real utils — __pycache__, removal residue, and stray files
    must not appear."""
    import subprocess as sp
    import sys

    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "adder", ADDER)
    (home / "utils" / "__pycache__").mkdir()
    (home / "utils" / ".ghost.removing.123").mkdir()
    (home / "utils" / "stray.txt").write_text("junk", encoding="utf-8")
    r = sp.run([sys.executable, str(home / "gu"), "list"],
               capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0
    assert "adder" in r.stdout
    assert "__pycache__" not in r.stdout and "removing" not in r.stdout
    assert "stray" not in r.stdout


def test_run_util_timeout_kills_grandchildren(tmp_path):
    """The timeout must kill the whole process GROUP: `uv run` re-execs the script as a
    grandchild that a plain kill leaves alive — holding the pipes open and blocking the
    engine turn forever. With killpg the call returns promptly and the tree is dead."""
    import os as _os
    import time as _time

    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    pidfile = tmp_path / "grandchild.pid"
    sleeper = f'''# /// script
# dependencies = []
# ///
"""sleeper — sleeps forever. usage: gu sleeper"""
import pathlib, sys, time
if "--selftest" in sys.argv:
    print("selftest: ok"); sys.exit(0)
pathlib.Path({str(pidfile)!r}).write_text(str(__import__("os").getpid()))
time.sleep(120)
'''
    utils_lib.write_util_file(home, "sleeper", sleeper)
    start = _time.monotonic()
    code, _out, err = utils_lib.run_util(home, "sleeper", [], timeout=3, policy=OFF)
    elapsed = _time.monotonic() - start
    assert code == -1 and "timed out" in err
    assert elapsed < 30, f"run_util blocked {elapsed:.0f}s past its timeout"
    # the grandchild (the uv-run python script) must be dead, not just the direct child.
    # "Dead" includes an unreaped ZOMBIE: in a containerized deployment whose pid1 is the
    # daemon itself (no reaping init), a SIGKILLed orphan stays in /proc state Z forever
    # and os.kill(pid, 0) still succeeds on it — kill-0 alone would false-fail (F148).
    def _dead(pid: int) -> bool:
        from pathlib import Path
        try:
            _os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            return stat.rsplit(") ", 1)[1].split()[0] == "Z"
        except (OSError, IndexError):
            return True   # /proc entry vanished between the two checks
    if pidfile.exists():
        pid = int(pidfile.read_text())
        for _ in range(50):   # killpg is async — give the kernel a moment
            if _dead(pid):
                break
            _time.sleep(0.1)
        else:
            raise AssertionError(f"grandchild {pid} survived the timeout kill")


def test_header_problems_flags_undeclared_gu_calls():
    """Code exec'ing a sibling via ["gu", "<name>"] without declaring it on `calls:` is
    rejected — transitive secret/net resolution only walks DECLARED calls."""
    base = ('"""caller — calls a sibling.\n\nusage: gu caller\ntags: test\n'
            'secrets: (none)\n{calls_line}net: none\n"""\n'
            'import subprocess\n'
            'subprocess.run(["gu", "adder", "1", "2"])\n')
    problems = utils_lib.header_problems(base.format(calls_line=""))
    assert any("adder" in p and "calls:" in p for p in problems)
    assert not utils_lib.header_problems(base.format(calls_line="calls: adder\n"))
