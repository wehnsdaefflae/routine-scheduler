"""Util library: real uv-run of utils (catalog, run, selftest, composition). Uses uv."""

import shutil

import pytest

from rsched import utils_lib

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv required to run utils")

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

    ok, out = utils_lib.selftest(home, "adder")
    assert ok, out
    code, stdout, _ = utils_lib.run_util(home, "adder", ["2", "3", "--json"])
    assert code == 0 and '"sum": 5' in stdout
    catalog = utils_lib.catalog_text(home)
    assert "adder — add two integers." in catalog


def test_util_composition(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "adder", ADDER)
    utils_lib.write_util_file(home, "doubler", DOUBLER)
    ok, out = utils_lib.selftest(home, "doubler")   # doubler calls adder via `gu`
    assert ok, out
    code, stdout, _ = utils_lib.run_util(home, "doubler", ["4", "--json"])
    assert code == 0 and '"double": 8' in stdout


def test_run_missing_and_invalid(tmp_path):
    home = tmp_path / "utils-home"
    utils_lib.ensure_library(home)
    code, _, err = utils_lib.run_util(home, "ghost", [])
    assert code == 2 and "no util named" in err
    code, _, err = utils_lib.run_util(home, "Bad Name", [])
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
    return SimpleNamespace(server=SimpleNamespace(utils_home=home), grants=grants)


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
    '"""\n'
    'import os\nkey = os.environ.get("DEMO_API_KEY")\n'
)


def test_header_problems_gate():
    """The util doc standard is enforced: tags required, and every credential-looking env
    var the code reads must be DECLARED in the docstring's secrets: line (a comment-form
    `# secrets:` above the docstring is invisible — the deepgram failure mode)."""
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
