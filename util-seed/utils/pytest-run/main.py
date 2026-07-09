# /// script
# dependencies = []
# ///
"""pytest-run — run a Python project's test suite and report pass/fail (routines have no shell).

usage: gu pytest-run REPO_PATH [--cmd "uv run pytest -q"] [--timeout SECS] [--json]
calls: (none)
tags: dev, testing, code

Runs the test suite in REPO_PATH and returns a structured verdict: ok (exit 0), the pytest
summary line, and the tail of output. Meant as the gate a self-modifying routine checks before
committing an edit to a project's own tree — a red suite must never be promoted. Default command
is `uv run --project REPO_PATH pytest -q`. --selftest exercises the output parser offline (it
does not spawn pytest)."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SUMMARY_RE = re.compile(r"\d+\s+(?:passed|failed|error|errors|skipped|xfailed|deselected|no tests ran)")


def parse_summary(stdout: str, stderr: str, exit_code: int) -> dict:
    """Extract pytest's final summary line + a pass/fail verdict. Exit code is the source of
    truth for ok (pytest exits non-zero on any failure/error); the summary is informational."""
    text = f"{stdout or ''}\n{stderr or ''}"
    lines = [ln.strip(" =") for ln in text.splitlines() if ln.strip()]
    summary = ""
    for ln in reversed(lines):
        if SUMMARY_RE.search(ln):
            summary = ln
            break
    return {"ok": exit_code == 0, "exit": exit_code,
            "summary": summary or (lines[-1] if lines else "")}


def run(repo_path: str, cmd: str = "", timeout: int = 600) -> dict:
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        raise ValueError(f"{repo} is not a directory")
    argv = cmd.split() if cmd else ["uv", "run", "--project", str(repo), "pytest", "-q"]
    proc = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
    result = parse_summary(proc.stdout, proc.stderr, proc.returncode)
    tail = (proc.stdout or "").splitlines()[-30:]
    result["tail"] = "\n".join(tail)
    result["repo"] = str(repo)
    return result


def selftest() -> int:
    green = parse_summary("===== 119 passed, 3 skipped, 1 warning in 6.44s =====", "", 0)
    assert green["ok"] and "119 passed" in green["summary"], green
    red = parse_summary("=== 1 failed, 118 passed, 3 skipped in 6.51s ===\nFAILED tests/x.py", "", 1)
    assert not red["ok"] and "1 failed" in red["summary"], red
    empty = parse_summary("no tests ran in 0.01s", "", 5)
    assert not empty["ok"] and "no tests ran" in empty["summary"], empty
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu pytest-run", description="Run a project's tests; report pass/fail.")
    p.add_argument("repo_path", nargs="?", help="path to the project (repo) root")
    p.add_argument("--cmd", default="", help="override the test command (default: uv run --project REPO pytest -q)")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not args.repo_path:
        p.error("provide REPO_PATH")
    try:
        result = run(args.repo_path, cmd=args.cmd, timeout=args.timeout)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    else:
        print(f"{'PASS' if result['ok'] else 'FAIL'} (exit {result['exit']}) — {result['summary']}")
    # exit non-zero when the suite is red, so a routine can gate on the util's own exit code too
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
