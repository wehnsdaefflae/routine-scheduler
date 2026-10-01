"""The gate's jail entry: answer every declarative check, then decide. Standard library only.

Run as `python -I run.py '<context json>'` inside the Landlock jail the daemon built from the
checks' own needs (`daemon/gate_prepare.prepare_checks`). `-I` keeps the interpreter from putting
this directory on the import path, so the entry puts it there itself — the `kit_*` siblings are
the only code the jail mounts.

Protocol: argv[1] is `{version: 2, routine, routine_dir, routines_home, libraries_home, now,
last_ok: {run_id, started, ended, fingerprints} | null, checks: [...]}`; stdout is
`{version: 1, decision, reason, checks: [{id, kind, work, reason, fingerprint?}]}`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kit_local
import kit_net
from kit_common import UnknownError

MAX_REASON = 1000

CHECKS = {
    "mail": kit_net.mail,
    "hub_feedback": kit_net.hub_feedback,
    "url_changed": kit_net.url_changed,
    "files_changed": kit_local.files_changed,
    "unpaired_files": kit_local.unpaired_files,
    "repo_changed": kit_local.repo_changed,
    "runs_since": kit_local.runs_since,
    "state": kit_local.state,
    "dates": kit_local.dates,
    "weekdays": kit_local.weekdays,
    "max_quiet": kit_local.max_quiet,
}


def evaluate(ctx: dict) -> dict:
    """Answer every declarative check and decide. Never raises: a check that errors is work."""
    results = []
    for i, check in enumerate(ctx.get("checks") or []):
        kind = check.get("kind")
        if kind == "script":
            continue                       # the daemon runs the custom predicate itself
        cid = str(check.get("id") or f"c{i + 1}")
        row: dict = {"id": cid, "kind": kind}
        try:
            work, reason, fingerprint = CHECKS[kind]({**check, "id": cid}, ctx)
        except UnknownError as exc:
            work, reason, fingerprint = True, f"could not check — {exc}", ""
        except Exception as exc:
            work, reason, fingerprint = True, f"could not check — {type(exc).__name__}: {exc}", ""
        row.update(work=bool(work), reason=reason[:300])
        if fingerprint:
            row["fingerprint"] = fingerprint
        results.append(row)
    working = [r for r in results if r["work"]]
    if working:
        reason = "; ".join(f"{r['kind']}: {r['reason']}" for r in working)
        decision = "run"
    elif results:
        reason = "Nothing to do — " + "; ".join(f"{r['kind']}: {r['reason']}" for r in results)
        decision = "skip"
    else:
        reason, decision = "no declarative checks", "skip"
    return {"version": 1, "decision": decision, "reason": _cap(reason), "checks": results}


def _cap(text: str) -> str:
    text = text.strip() or "no reason given"
    return text if len(text) <= MAX_REASON else text[:MAX_REASON - 1] + "…"


def main(argv: list[str]) -> int:
    try:
        out = evaluate(json.loads(argv[1]))
    except Exception as exc:
        out = {"version": 1, "decision": "run", "checks": [],
               "reason": _cap(f"the gate could not read its own configuration ({exc}) — "
                              "running rather than guessing")}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
