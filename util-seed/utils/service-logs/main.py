# /// script
# dependencies = []
# ///
"""service-logs — read the scheduler daemon's systemd user journal (routines have no shell).

usage: gu service-logs [--since "24 hours ago"] [--lines N] [--grep PATTERN] [--unit UNIT] [--json]
calls: (none)
secrets: (none)
tags: logs, daemon, health
net: none

Wraps `journalctl --user -u routine-scheduler.service` for routine consumption: entries since
--since (default the last 24 hours), capped at --lines (default 500), optionally filtered with
--grep (journalctl's own -g, a case-insensitive regex). With --json emits
{"entries": [{"ts", "priority", "message"}, ...], "counts": {"err": n, "warning": n, ...}} so a
run can scan for errors without parsing prose. Degrades cleanly where journalctl is unavailable
(e.g. a container): a clear error object and a non-zero exit. --selftest exercises arg mapping,
the entry parser, and the journalctl-missing path offline — no real journal is needed."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

DEFAULT_UNIT = "routine-scheduler.service"
PRIORITY_NAMES = {"0": "emerg", "1": "alert", "2": "crit", "3": "err",
                  "4": "warning", "5": "notice", "6": "info", "7": "debug"}


def build_cmd(since: str, lines: int, grep: str, unit: str, binary: str = "journalctl") -> list[str]:
    """The exact journalctl argv for the given flags. Pure; testable offline."""
    cmd = [binary, "--user", "-u", unit, "--since", since,
           "-n", str(max(1, lines)), "-o", "json", "--no-pager"]
    if grep:
        cmd += ["-g", grep]
    return cmd


def parse_entries(raw_lines: list[str]) -> tuple[list[dict], dict]:
    """journalctl `-o json` lines → ({ts, priority, message} entries, counts by priority name).
    Pure; testable offline. Unparseable lines are skipped."""
    entries: list[dict] = []
    counts: dict[str, int] = {}
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        prio = PRIORITY_NAMES.get(str(obj.get("PRIORITY", "")), "unknown")
        msg = obj.get("MESSAGE", "")
        if not isinstance(msg, str):                     # binary payloads arrive as byte arrays
            msg = str(msg)
        ts = ""
        usec = obj.get("__REALTIME_TIMESTAMP")
        if usec:
            try:
                ts = datetime.fromtimestamp(int(usec) / 1e6, tz=timezone.utc).isoformat(timespec="seconds")
            except (ValueError, OSError, OverflowError):
                ts = str(usec)
        entries.append({"ts": ts, "priority": prio, "message": msg})
        counts[prio] = counts.get(prio, 0) + 1
    return entries, counts


def run(since: str = "24 hours ago", lines: int = 500, grep: str = "",
        unit: str = DEFAULT_UNIT, binary: str = "journalctl") -> dict:
    """Query the journal; returns the structured result, or {"error": ...} when it can't."""
    cmd = build_cmd(since, lines, grep, unit, binary)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return {"error": f"{binary} not found — no systemd journal is available on this host",
                "unit": unit}
    except subprocess.TimeoutExpired:
        return {"error": f"{binary} timed out after 60s", "unit": unit}
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()[:300]
        return {"error": f"{binary} exited {r.returncode}: {detail or 'no detail'}", "unit": unit}
    entries, counts = parse_entries(r.stdout.splitlines())
    return {"unit": unit, "since": since, "entries": entries, "counts": counts}


def selftest() -> int:
    # 1. Arg parsing → journalctl argv mapping.
    args = build_parser().parse_args(["--since", "1 hour ago", "--lines", "50", "--grep", "ERROR"])
    assert (args.since, args.lines, args.grep, args.unit) == ("1 hour ago", 50, "ERROR", DEFAULT_UNIT)
    cmd = build_cmd(args.since, args.lines, args.grep, args.unit)
    assert cmd == ["journalctl", "--user", "-u", DEFAULT_UNIT, "--since", "1 hour ago",
                   "-n", "50", "-o", "json", "--no-pager", "-g", "ERROR"], cmd
    assert "-g" not in build_cmd("24 hours ago", 500, "", DEFAULT_UNIT)
    # 2. Entry parsing + priority counts, against fixture journal lines.
    fixture = [
        json.dumps({"PRIORITY": "3", "MESSAGE": "run overran its budget",
                    "__REALTIME_TIMESTAMP": "1767225600000000"}),
        json.dumps({"PRIORITY": "6", "MESSAGE": "scheduler tick"}),
        "not-json-at-all",
    ]
    entries, counts = parse_entries(fixture)
    assert len(entries) == 2 and counts == {"err": 1, "info": 1}, (entries, counts)
    assert entries[0]["priority"] == "err" and entries[0]["ts"].startswith("2026-01-01")
    # 3. The journalctl-missing path: a clear error object, no exception.
    result = run(binary="journalctl-selftest-definitely-missing")
    assert "error" in result and "not found" in result["error"], result
    print("selftest: ok", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gu service-logs",
                                description="Read the scheduler daemon's systemd user journal.")
    p.add_argument("--since", default="24 hours ago",
                   help='journalctl --since expression (default "24 hours ago")')
    p.add_argument("--lines", type=int, default=500, help="max entries returned (default 500)")
    p.add_argument("--grep", default="", help="filter messages by this regex (journalctl -g)")
    p.add_argument("--unit", default=DEFAULT_UNIT, help=f"systemd user unit (default {DEFAULT_UNIT})")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.selftest:
        return selftest()
    result = run(since=args.since, lines=args.lines, grep=args.grep, unit=args.unit)
    if "error" in result:
        print(json.dumps(result) if args.json else f"error: {result['error']}",
              file=sys.stdout if args.json else sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    else:
        for e in result["entries"]:
            print(f"{e['ts']} [{e['priority']}] {e['message']}")
        summary = ", ".join(f"{k}={v}" for k, v in sorted(result["counts"].items())) or "no entries"
        print(f"-- {len(result['entries'])} entries since {result['since']!r} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
