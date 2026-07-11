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
    import json as _json

    from rsched.engine.composer import format_observation
    obs = {"kind": "util", "name": "demo", "args": [], "exit": 2,
           "stdout": "", "stderr": "usage error",
           "usage": "usage: gu demo TARGET [--json]",
           "hint": 'pass every argument in `args` as a JSON array of strings'}
    text = format_observation(obs)
    assert "[usage] usage: gu demo TARGET" in text and "[hint]" in text
