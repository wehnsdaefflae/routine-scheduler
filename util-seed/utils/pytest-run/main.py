# /// script
# dependencies = []
# ///
"""pytest-run — run a Python project's test suite and report pass/fail (routines have no shell).

usage: gu pytest-run REPO_PATH [--cmd "uv run pytest -q"] [--timeout SECS] [--json]
calls: (none)
tags: dev, testing, code
net: outbound
fs: roots

Runs the test suite in REPO_PATH and returns a structured verdict: ok (exit 0), the pytest
summary line, and the tail of output. Meant as the gate a self-modifying routine checks before
committing an edit to a project's own tree — a red suite must never be promoted. Default command
is `uv run --project REPO_PATH pytest -q`.

exit codes: 0 = green suite · 1 = red suite (still non-zero, so a routine gating on !=0 is
unaffected) · 2 = bad arguments (argparse). Red suites deliberately exit 1, NOT 2: exit 2 is the
util-contract's reserved bad-args code, and the scheduler buckets exit-2 as a caller usage_error —
so a red gate-suite exiting 2 was being miscounted as util misuse. Exit 1 keeps the gate working
while freeing exit 2 for genuine argument errors.

If that default `uv run` fails while PREPARING the environment rather than running tests — e.g.
the project's uv env is an operator-provisioned venv the caller mounts read-only, so uv's implicit
sync hits `Permission denied` before pytest starts — the util retries ONCE with `uv run --no-sync`
(use the env exactly as provisioned, do not mutate it). A normal red suite is untouched by this:
it always carries a pytest summary line, so the retry only fires on a genuine uv env/sync error.

--selftest exercises the output parser + the sync-failure detector offline (it does not spawn pytest)."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SUMMARY_RE = re.compile(r"\d+\s+(?:passed|failed|error|errors|skipped|xfailed|deselected|no tests ran)")

# uv env-preparation failure markers (uv couldn't build/sync the env — NOT a test outcome).
_UV_ENV_FAIL_MARKERS = (
    "failed to remove",
    "failed to sync",
    "failed to prepare",
    "failed to install",
    "permission denied",
    "read-only file system",
)


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


def looks_like_uv_env_failure(stdout: str, stderr: str) -> bool:
    """True when uv failed to PREPARE the environment (so pytest never ran): a uv `error:` line
    plus a sync/permission/read-only marker, and NO real pytest summary line anywhere in the
    output. The pytest-summary check distinguishes a broken env from a red test suite (a red
    suite always prints e.g. '1 failed, 118 passed ...')."""
    blob = f"{stdout or ''}\n{stderr or ''}".lower()
    if SUMMARY_RE.search(blob):        # pytest ran and reported → a test result, not an env failure
        return False
    if "error:" not in blob:
        return False
    return any(m in blob for m in _UV_ENV_FAIL_MARKERS)


def run(repo_path: str, cmd: str = "", timeout: int = 600) -> dict:
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        raise ValueError(f"{repo} is not a directory")
    default = not cmd
    argv = cmd.split() if cmd else ["uv", "run", "--project", str(repo), "pytest", "-q"]
    proc = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
    result = parse_summary(proc.stdout, proc.stderr, proc.returncode)

    retried = False
    if default and not result["ok"] and looks_like_uv_env_failure(proc.stdout, proc.stderr):
        # The env couldn't be synced (e.g. read-only project venv). Use it as provisioned.
        argv = ["uv", "run", "--no-sync", "--project", str(repo), "pytest", "-q"]
        proc = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
        result = parse_summary(proc.stdout, proc.stderr, proc.returncode)
        retried = True

    tail = (proc.stdout or "").splitlines()[-30:]
    result["tail"] = "\n".join(tail)
    result["repo"] = str(repo)
    if retried:
        result["retried_no_sync"] = True
    return result


def selftest() -> int:
    green = parse_summary("===== 119 passed, 3 skipped, 1 warning in 6.44s =====", "", 0)
    assert green["ok"] and "119 passed" in green["summary"], green
    red = parse_summary("=== 1 failed, 118 passed, 3 skipped in 6.51s ===\nFAILED tests/x.py", "", 1)
    assert not red["ok"] and "1 failed" in red["summary"], red
    empty = parse_summary("no tests ran in 0.01s", "", 5)
    assert not empty["ok"] and "no tests ran" in empty["summary"], empty

    # sync-failure detector: a uv env/permission error with NO pytest summary → True
    env_fail = ("error: failed to remove file "
                "`/opt/rsched-venv/lib/python3.12/site-packages/../../../bin/rsched`: "
                "Permission denied (os error 13)")
    assert looks_like_uv_env_failure("", env_fail) is True
    # a normal red suite (has a summary line) must NOT be treated as an env failure
    red_blob = "=== 1 failed, 118 passed in 6.5s ===\nFAILED tests/x.py"
    assert looks_like_uv_env_failure(red_blob, "") is False
    # an unrelated non-error stderr must NOT trip it
    assert looks_like_uv_env_failure("", "warning: VIRTUAL_ENV ignored") is False
    # a uv error but WITH a pytest summary present (edge) → treat as test result, not env failure
    assert looks_like_uv_env_failure("2 failed, 3 passed in 1s", env_fail) is False
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
        note = " (retried --no-sync)" if result.get("retried_no_sync") else ""
        print(f"{'PASS' if result['ok'] else 'FAIL'} (exit {result['exit']}){note} — {result['summary']}")
    # red suite → exit 1 (non-zero, so a routine can still gate on the util's own exit code);
    # exit 2 stays reserved for genuine bad-args (argparse) and is NOT emitted for a red suite.
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
