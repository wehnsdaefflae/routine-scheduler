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

`--timeout` DEFAULTS from the deadline this call was given (RSCHED_UTIL_TIMEOUT_S, less the
grace and a margin; 600 when run by hand), so a suite that overruns is ended HERE and the
result says so — `timed_out: true`, exit -1, with everything the suite printed up to then —
instead of the whole util being ended with nothing to show. Ending it means its whole process
group: SIGTERM to pytest and everything it started, SIGKILL 30 s later for what ignores that.

If that default `uv run` fails while PREPARING the environment rather than running tests — e.g.
the project's uv env is an operator-provisioned venv the caller mounts read-only, so uv's implicit
sync hits `Permission denied` before pytest starts — the util retries ONCE with `uv run --no-sync`
(use the env exactly as provisioned, do not mutate it). A normal red suite is untouched by this:
it always carries a pytest summary line, so the retry only fires on a genuine uv env/sync error.

--selftest exercises the output parser + the sync-failure detector offline, and the deadline on
a stand-in suite that never finishes (it does not spawn pytest)."""

import argparse
import contextlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: The suite's deadline when nothing exported the caller's own (a call made by hand).
DEFAULT_TIMEOUT_S = 600
#: Between SIGTERM and SIGKILL for a suite being ended — the scheduler's own grace
#: (rsched.procgroup.TERM_GRACE_S): a test that runs git needs it to delete its index.lock.
TERM_GRACE_S = 30
#: What is left of the call's deadline once the suite's has passed and the grace is spent: room
#: to read the capture and print the verdict before the scheduler ends this util.
REPORT_MARGIN_S = 15


def default_timeout() -> int:
    """The `--timeout` default: the scheduler's deadline for this call (RSCHED_UTIL_TIMEOUT_S,
    exported by both util runners) less the grace an ended suite gets and a margin to report
    in — so the suite's own deadline always passes first. Two clocks that raced is what R1813
    cost `remote exec`; here the old fixed 600 s outlasted the 300 s every util call gets by
    default, so a slow suite was ended by the scheduler and its caller got nothing at all.
    """
    try:
        budget = int(os.environ.get("RSCHED_UTIL_TIMEOUT_S", ""))
    except ValueError:
        return DEFAULT_TIMEOUT_S
    if budget <= 0:
        return DEFAULT_TIMEOUT_S
    return max(budget - TERM_GRACE_S - REPORT_MARGIN_S, 10)


SUMMARY_RE =re.compile(r"\d+\s+(?:passed|failed|error|errors|skipped|xfailed|deselected|no tests ran)")

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


def _end_group(proc: subprocess.Popen) -> None:
    """End the process group `proc` leads the way `rsched.procgroup.terminate` does (which a uv
    script cannot import): SIGTERM to every member, up to TERM_GRACE_S for ALL of them to exit,
    SIGKILL for whatever is left. The leader is reaped either way."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_S
    while True:
        proc.poll()                    # reap the leader first: its zombie answers for the group
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return
        time.sleep(0.05)


def run_suite(argv: list, cwd: Path, timeout: float) -> tuple:
    """Run the test command → (exit code, or None when it was ended at `timeout`; stdout;
    stderr) — everything it printed, either way.

    The command leads a process group of its own, because ending it has to reach the suite:
    `uv run` re-execs pytest as its CHILD and a suite starts servers and browsers of its own,
    so the SIGKILL `subprocess.run` sends at its timeout reached `uv` alone and left the suite
    running on with no deadline at all. It is ended through `_end_group`. The capture goes to
    files rather than pipes, so a member that survives holding them cannot stall the read.

    A SIGTERM this util receives — the scheduler ending the call, or its run being aborted — is
    sent to the util's OWN group, which the suite does not share; it is passed on, the suite
    ends, and the verdict reports what it printed before it did.
    """
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out_f, \
            tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err_f:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=out_f,
                                stderr=err_f, start_new_session=True)

        def pass_on(signum, _frame):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signum)

        previous = signal.signal(signal.SIGTERM, pass_on)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _end_group(proc)
            code = None
        except BaseException:
            _end_group(proc)
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)
        out_f.seek(0)
        err_f.seek(0)
        return code, out_f.read(), err_f.read()


def run(repo_path: str, cmd: str = "", timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        raise ValueError(f"{repo} is not a directory")
    default = not cmd
    argv = split_cmd(cmd) if cmd else ["uv", "run", "--project", str(repo), "pytest", "-q"]
    deadline = time.monotonic() + timeout
    code, out, err = run_suite(argv, repo, timeout)

    retried = False
    if (default and code not in (0, None) and looks_like_uv_env_failure(out, err)
            and time.monotonic() < deadline):
        # The env couldn't be synced (e.g. read-only project venv). Use it as provisioned —
        # within what is left of the same deadline, never a second one of its own.
        argv = ["uv", "run", "--no-sync", "--project", str(repo), "pytest", "-q"]
        code, out, err = run_suite(argv, repo, deadline - time.monotonic())
        retried = True

    if code is None:
        result = {"ok": False, "exit": -1, "timed_out": True,
                  "summary": f"timed out after {timeout:g}s — the suite was ended before it "
                             "finished; what it printed until then is below"}
    else:
        result = parse_summary(out, err, code)

    # Tail BOTH streams: a runner that writes its diagnostics to stderr (node, custom harnesses)
    # was previously reported as a bare failure with no cause visible.
    result["tail"] = "\n".join(out.splitlines()[-30:])
    result["stderr_tail"] = "\n".join(err.splitlines()[-30:])
    # On a RED run, the failure section is the whole point of running a test gate.
    if not result["ok"]:
        result["failure_section"] = extract_failure_section(out, err)
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

    # --- ONE clock (R1813): the suite's deadline sits inside the scheduler's for this call ---
    saved = os.environ.pop("RSCHED_UTIL_TIMEOUT_S", None)
    try:
        os.environ["RSCHED_UTIL_TIMEOUT_S"] = "300"
        assert default_timeout() == 300 - TERM_GRACE_S - REPORT_MARGIN_S, default_timeout()
        os.environ["RSCHED_UTIL_TIMEOUT_S"] = "20"
        assert default_timeout() == 10, "a tiny budget still leaves the suite a floor"
        for junk in ("", "soon", "0"):
            os.environ["RSCHED_UTIL_TIMEOUT_S"] = junk
            assert default_timeout() == DEFAULT_TIMEOUT_S, junk
    finally:
        os.environ.pop("RSCHED_UTIL_TIMEOUT_S", None)
        if saved is not None:
            os.environ["RSCHED_UTIL_TIMEOUT_S"] = saved

    # --- a suite that overruns is ENDED, whole, and REPORTED with what it printed ---
    def alive(pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    with tempfile.TemporaryDirectory() as tmp:
        pidfile = os.path.join(tmp, "child.pid")
        stand_in = ("import subprocess, time; print('collected 3 items', flush=True); "
                    "p = subprocess.Popen(['sleep', '60']); "
                    f"open({pidfile!r}, 'w').write(str(p.pid)); time.sleep(60)")
        started = time.monotonic()
        res = run(tmp, cmd=shlex.join([sys.executable, "-c", stand_in]), timeout=5)
        assert time.monotonic() - started < TERM_GRACE_S, "the suite outlived its SIGTERM"
        assert res["timed_out"] is True and res["ok"] is False and res["exit"] == -1, res
        assert "collected 3 items" in res["tail"], "what the suite printed must survive"
        assert os.path.exists(pidfile), "the stand-in suite never started its child"
        with open(pidfile, encoding="utf-8") as fh:
            child = int(fh.read())
        assert not alive(child), "a process the suite started outlived its deadline"

    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gu pytest-run", description="Run a project's tests; report pass/fail.")
    p.add_argument("repo_path", nargs="?", help="path to the project (repo) root")
    p.add_argument("--cmd", default="", help="override the test command (default: uv run --project REPO pytest -q)")
    p.add_argument("--timeout", type=int, default=default_timeout(),
                   help="seconds before the suite is ended (default: this call's own deadline, "
                        "less the grace and a margin, so the timeout is REPORTED with the "
                        "output so far instead of the util being killed; 600 by hand)")
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
        verdict = "TIMEOUT" if result.get("timed_out") else "PASS" if result["ok"] else "FAIL"
        print(f"{verdict} (exit {result['exit']}){note} — {result['summary']}")
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
