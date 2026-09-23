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
import shlex
import subprocess
import sys
from pathlib import Path

SUMMARY_RE = re.compile(r"\d+\s+(?:passed|failed|error|errors|skipped|xfailed|deselected|no tests ran)")

# Start of pytest's detailed failure block, e.g. "=========== FAILURES ===========" or
# "=========== ERRORS ===========". This is the part a caller needs to know WHY it was red.
FAILURE_HEADING_RE = re.compile(r"^=+\s*(FAILURES|ERRORS)\s*=+\s*$")
# Start of the trailing recap, e.g. "==== short test summary info ====".
SHORT_SUMMARY_RE = re.compile(r"^=+\s*short test summary info\s*=+\s*$")

FAILURE_SECTION_CAP = 12000  # chars; a runaway traceback must not flood the caller's observation


def extract_failure_section(stdout: str, stderr: str, cap: int = FAILURE_SECTION_CAP) -> str:
    """Return pytest's failure detail — from '=== FAILURES ===' (or ERRORS) through the short
    summary — so a red run SHOWS why it was red instead of only its count line. Falls back to the
    short-summary block alone when the full section is absent (e.g. --tb=no), and to '' when the
    output carries no failure detail at all. Truncated in the MIDDLE (head+tail kept), because a
    traceback's first frames and its final assertion line are both load-bearing."""
    text = f"{stdout or ''}\n{stderr or ''}"
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if FAILURE_HEADING_RE.match(ln.strip()):
            start = i
            break
    if start is None:
        for i, ln in enumerate(lines):
            if SHORT_SUMMARY_RE.match(ln.strip()):
                start = i
                break
    if start is None:
        return ""
    section = "\n".join(lines[start:]).strip()
    if len(section) <= cap:
        return section
    head = section[: cap // 2]
    tail = section[-(cap // 2) :]
    dropped = len(section) - len(head) - len(tail)
    return f"{head}\n\n... [{dropped} chars of failure output elided] ...\n\n{tail}"


def split_cmd(cmd: str) -> list:
    """Split a --cmd string the way a shell would, so quoted inner commands survive intact —
    e.g. --cmd "bash -c 'pytest -q > out.txt; cat out.txt'" becomes three argv entries, not seven.
    A plain str.split() mangled these, producing an unbalanced-quote error from the inner shell.
    Raises ValueError (loudly, naming the problem) on an unbalanced quote rather than guessing."""
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        raise ValueError(
            f"--cmd is not a valid command line ({exc}): {cmd!r}. "
            "Quote inner commands normally, e.g. --cmd \"bash -c 'pytest -q'\""
        ) from exc
    if not argv:
        raise ValueError("--cmd is empty")
    return argv

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
    argv = split_cmd(cmd) if cmd else ["uv", "run", "--project", str(repo), "pytest", "-q"]
    proc = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
    result = parse_summary(proc.stdout, proc.stderr, proc.returncode)

    retried = False
    if default and not result["ok"] and looks_like_uv_env_failure(proc.stdout, proc.stderr):
        # The env couldn't be synced (e.g. read-only project venv). Use it as provisioned.
        argv = ["uv", "run", "--no-sync", "--project", str(repo), "pytest", "-q"]
        proc = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
        result = parse_summary(proc.stdout, proc.stderr, proc.returncode)
        retried = True

    # Tail BOTH streams: a runner that writes its diagnostics to stderr (node, custom harnesses)
    # was previously reported as a bare failure with no cause visible.
    out_tail = (proc.stdout or "").splitlines()[-30:]
    err_tail = (proc.stderr or "").splitlines()[-30:]
    result["tail"] = "\n".join(out_tail)
    result["stderr_tail"] = "\n".join(err_tail)
    # On a RED run, the failure section is the whole point of running a test gate.
    if not result["ok"]:
        result["failure_section"] = extract_failure_section(proc.stdout, proc.stderr)
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

    # --- failure-section extraction (R1414/R1404: a red run must show WHY) ---
    red_output = (
        "collected 757 items\n"
        "........F\n"
        "=================================== FAILURES ===================================\n"
        "____________________________ test_ledger_roundtrip _____________________________\n"
        "    def test_ledger_roundtrip():\n"
        ">       assert got == want\n"
        "E       AssertionError: assert 3 == 4\n"
        "bin/test_ledger.py:814: AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED bin/test_ledger.py::test_ledger_roundtrip - AssertionError: assert 3 == 4\n"
        "1 failed, 756 passed in 21.09s\n"
    )
    sec = extract_failure_section(red_output, "")
    assert "FAILURES" in sec and "bin/test_ledger.py:814: AssertionError" in sec, sec
    assert "collected 757 items" not in sec, "section must start at FAILURES, not at collection"
    # --tb=no style output: no FAILURES block, but the short summary still names the test
    sec2 = extract_failure_section(
        "=========================== short test summary info ============================\n"
        "FAILED bin/test_ledger.py::test_x - AssertionError\n1 failed, 2 passed in 1s\n", "")
    assert sec2.startswith("=") and "FAILED bin/test_ledger.py::test_x" in sec2, sec2
    # a green run carries no failure detail at all
    assert extract_failure_section("119 passed in 6.44s", "") == ""
    # stderr-only diagnostics (node/custom harness) are searched too
    sec3 = extract_failure_section("", "=== ERRORS ===\nReferenceError: foo is not defined\n")
    assert "ReferenceError" in sec3, sec3
    # a runaway traceback is capped but keeps BOTH ends
    huge = "=== FAILURES ===\n" + ("X" * 40000) + "\nE   AssertionError: tail-marker\n"
    capped = extract_failure_section(huge, "")
    assert len(capped) < 14000 and "elided" in capped, len(capped)
    assert "FAILURES" in capped and "tail-marker" in capped, "head and tail must both survive"

    # --- --cmd passthrough (R1414 defect 2: quoted inner commands were re-split) ---
    assert split_cmd("bash -c 'pytest -q > out.txt 2>&1; cat out.txt'") == [
        "bash", "-c", "pytest -q > out.txt 2>&1; cat out.txt"], split_cmd(
        "bash -c 'pytest -q > out.txt 2>&1; cat out.txt'")
    assert split_cmd("venv/bin/python -m pytest -q") == [
        "venv/bin/python", "-m", "pytest", "-q"]
    assert split_cmd('pytest -k "not slow"') == ["pytest", "-k", "not slow"]
    # an unbalanced quote must FAIL LOUDLY naming the problem, never be silently mangled
    try:
        split_cmd("bash -c 'pytest -q")
    except ValueError as exc:
        assert "--cmd is not a valid command line" in str(exc), exc
    else:
        raise AssertionError("unbalanced quote must raise, not mangle")
    try:
        split_cmd("   ")
    except ValueError as exc:
        assert "empty" in str(exc), exc
    else:
        raise AssertionError("empty --cmd must raise")

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
        if not result["ok"]:
            # A test runner that hides the failure message makes the caller do the very work the
            # runner exists to avoid, so print the cause on stderr (stdout stays the verdict line).
            section = result.get("failure_section") or ""
            if section:
                print(section, file=sys.stderr)
            elif result.get("tail"):
                print(result["tail"], file=sys.stderr)
            if result.get("stderr_tail"):
                print("--- stderr ---", file=sys.stderr)
                print(result["stderr_tail"], file=sys.stderr)
    # red suite → exit 1 (non-zero, so a routine can still gate on the util's own exit code);
    # exit 2 stays reserved for genuine bad-args (argparse) and is NOT emitted for a red suite.
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
