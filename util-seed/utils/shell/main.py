# /// script
# dependencies = []
# ///
"""shell — run ONE shell command on the host (reserved: needs the shell permission).

usage: gu shell COMMAND [--timeout SECS] [--cwd DIR] [--json]
calls: (none)
tags: shell, system, escape-hatch
net: outbound

The deliberate escape hatch around the no-shell design, reserved for routines holding the
`shell` permission (the engine rejects it for everyone else). Runs COMMAND through
`bash -c` non-interactively, captures stdout/stderr, and reports the exit code — the
util's own exit mirrors the command's, so failures are visible to the caller. Output is
capped (64 KB per stream, head+tail) so a chatty command can never flood a transcript.
Anything you would run twice belongs in a proper util, not here.
--selftest runs a fixed echo pipeline, offline."""

import argparse
import json
import subprocess
import sys

CAP = 64_000


def _capped(text: str) -> tuple[str, bool]:
    if len(text) <= CAP:
        return text, False
    head, tail = int(CAP * 0.7), CAP - int(CAP * 0.7)
    return (text[:head] + f"\n[... {len(text) - CAP} chars omitted (head+tail kept) ...]\n"
            + text[-tail:]), True


def run(command: str, timeout: int = 120, cwd: str | None = None) -> dict:
    if not command.strip():
        raise ValueError("empty command")
    try:
        proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
        stdout, out_trunc = _capped(proc.stdout)
        stderr, err_trunc = _capped(proc.stderr)
        return {"command": command, "exit": proc.returncode, "stdout": stdout,
                "stderr": stderr, "truncated": out_trunc or err_trunc, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "exit": 124,
                "stdout": _capped(exc.stdout or "")[0] if isinstance(exc.stdout, str) else "",
                "stderr": _capped(exc.stderr or "")[0] if isinstance(exc.stderr, str) else "",
                "truncated": False, "timed_out": True}


def _selftest() -> int:
    result = run("printf 'a\\nb\\n' | wc -l")
    assert result["exit"] == 0 and result["stdout"].strip() == "2", result
    result = run("exit 3")
    assert result["exit"] == 3, result
    result = run("sleep 5", timeout=1)
    assert result["timed_out"] and result["exit"] == 124, result
    print("selftest: ok", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gu shell", add_help=True)
    parser.add_argument("command", nargs="?", help="the shell command to run (one string)")
    parser.add_argument("--timeout", type=int, default=120, help="seconds before the command is killed")
    parser.add_argument("--cwd", default=None, help="working directory for the command")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    if not args.command or not args.command.strip():
        print("usage: gu shell COMMAND [--timeout SECS] [--cwd DIR] [--json]", file=sys.stderr)
        return 2
    try:
        result = run(args.command, timeout=args.timeout, cwd=args.cwd)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("usage: gu shell COMMAND [--timeout SECS] [--cwd DIR] [--json]", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if result["stdout"]:
            print(result["stdout"], end="" if result["stdout"].endswith("\n") else "\n")
        if result["stderr"]:
            print(result["stderr"], file=sys.stderr,
                  end="" if result["stderr"].endswith("\n") else "\n")
        if result["timed_out"]:
            print(f"[timed out after {args.timeout}s]", file=sys.stderr)
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
