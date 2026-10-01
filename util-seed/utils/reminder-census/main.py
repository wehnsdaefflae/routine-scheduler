# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""reminder-census — every consequence reminder on this instance, with the evidence that decides its fate.

usage: gu reminder-census [--routines-home PATH] [--library PATH] [--json]
       gu reminder-census --selftest
calls: (none)
secrets: (none)
tags: reminders, audit, meta
net: none
fs: roots

Reads every routine's own reminders (`<routines>/<slug>/state/reminders.json`) and the curated
store (`<library>/reminders/*.json`), then every retained transcript (`runs/*/transcript.jsonl`
and `.jsonl.gz`, child runs under `sub/` included at any depth). It reports per reminder:

- `holds` and `labels` recounted from the transcripts, ONE label per hold: a label with no hold
  of that reminder before it in the same run is `unanchored` and counts for nothing (the stored
  tally once read 5 fires and 77 `would_have` for one reminder);
- `own` / `others`: how many actions of its own routine and of every OTHER routine its pattern
  matches, replayed against the canonical one-line rendering of each action — what a curated
  copy would start holding and where;
- `problems`: a pattern matching no action anywhere, an anchor on a util the library no longer
  holds, a util revised after the reminder was written (its premise needs re-checking);
- `clusters`: reminders of different routines anchored on the same util or matching the same
  actions — one caution authored independently more than once.

It decides nothing: which reminder is promoted, narrowed, routed to its util's owner or deleted
is the curator's judgement. The canonical rendering here must equal the engine's
(`rsched.engine.actionschema.canon`); the routine-scheduler test suite pins the two together.
Data on stdout (JSON with --json, a table otherwise); diagnostics on stderr."""

import argparse
import gzip
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LABELS = ("could_not", "would_have", "did", "didnt")
MATCH_TARGET_CHARS = 2_000
#: The identifying field of each action kind — the engine's BRIEF_FIELD, kept equal by test.
BRIEF_FIELD = {"util": "name", "write_util": "name", "remove_util": "name", "read_file": "path",
               "view_image": "path", "script": "name", "shell": "command",
               "write_file": "path", "delete": "path", "move": "src",
               "mkdir": "path", "edit_file": "path", "memory_read": "name",
               "memory_write": "name", "read_rule": "name", "write_rule": "name",
               "llm": "prompt", "spawn": "label", "subtask": "label",
               "detach": "label", "schedule_run": "target", "create_routine": "target",
               "manage_lane": "verb",
               "kill": "n", "wait": "n",
               "ask_user": "question", "report": "title", "finish": "status"}
_UTIL_ANCHOR = re.compile(r"^\^util:([a-z0-9][a-z0-9-]*)")


def canon(action: dict) -> str:
    """The engine's canonical rendering of one action (see the module docstring)."""
    kind = str(action.get("kind") or "?")
    if kind in ("util", "script"):
        args = action.get("args")
        tail = " ".join(str(a) for a in args) if isinstance(args, list) else ""
        return f"{kind}:{action.get('name') or '?'}{f' {tail}' if tail else ''}"
    if kind == "shell":
        return f"shell: {action.get('command') or ''}".rstrip()
    if kind == "read_file" and isinstance(action.get("paths"), list) and action["paths"]:
        return f"read_file paths={','.join(str(x) for x in action['paths'])}"
    field = BRIEF_FIELD.get(kind, "")
    value = str(action.get(field, "") or "") if field else ""
    return f"{kind} {field}={value}" if value else kind


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _events(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError as exc:
        print(f"reminder-census: cannot read {path}: {exc}", file=sys.stderr)


def reminders(routines: Path, library: Path) -> list[dict]:
    out = []
    for path in sorted(routines.glob("*/state/reminders.json")):
        doc = _read_json(path)
        for rec in (doc or {}).get("reminders") or [] if isinstance(doc, dict) else []:
            if isinstance(rec, dict) and rec.get("id") and rec.get("regex"):
                out.append({"id": rec["id"], "routine": path.parents[1].name, "scope": "local",
                            "regex": rec["regex"], "description": rec.get("description", ""),
                            "created_run": rec.get("created_run", ""),
                            "stored": rec.get("stats") or {}})
    for path in sorted((library / "reminders").glob("*.json")):
        rec = _read_json(path)
        if isinstance(rec, dict) and rec.get("regex"):
            out.append({"id": rec.get("id") or path.stem, "routine": "", "scope": "global",
                        "reach": rec.get("reach", ""), "regex": rec["regex"],
                        "description": rec.get("description", ""),
                        "created_run": rec.get("created_run", ""), "stored": {}})
    return out


def transcripts(routines: Path) -> dict[str, list[Path]]:
    """Every retained transcript per routine — a child run's too, at any depth: a child's own
    children sit under ITS `sub/`, and a census that stopped one level down never saw their
    holds or labels."""
    by_routine: dict[str, list[Path]] = defaultdict(list)
    for rdir in sorted(p for p in routines.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for pattern in ("runs/*/transcript.jsonl*", "runs/*/sub/**/transcript.jsonl*"):
            by_routine[rdir.name] += sorted(rdir.glob(pattern))
    return by_routine


def census(routines: Path, library: Path) -> dict:
    rems = reminders(routines, library)
    compiled = {}
    for r in rems:
        try:
            compiled[(r["routine"], r["id"])] = re.compile(r["regex"])
        except re.error:
            r["problems"] = ["the pattern does not compile"]
    matches: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    anchored: dict[tuple, dict] = defaultdict(lambda: dict.fromkeys(("holds", "unanchored",
                                                                      *LABELS), 0))
    local_ids = {(r["routine"], r["id"]) for r in rems if r["scope"] == "local"}
    for routine, paths in transcripts(routines).items():
        for path in paths:
            owed: dict[str, int] = defaultdict(int)
            for ev in _events(path):
                payload = ev.get("payload") if isinstance(ev, dict) else None
                if not isinstance(payload, dict):
                    continue
                if ev.get("type") == "observation" and payload.get("kind") == "reminder_hold":
                    for held in payload.get("reminders") or []:
                        rid = str(held.get("id") or "")
                        owed[rid] += 1
                        key = (routine if held.get("scope") == "local" else "", rid)
                        anchored[key]["holds"] += 1
                if ev.get("type") != "assistant_action":
                    continue
                fb = payload.get("remind_feedback")
                if isinstance(fb, dict) and fb.get("label") in LABELS:
                    rid = str(fb.get("id") or "")
                    key = (routine if (routine, rid) in local_ids else "", rid)
                    if owed[rid] > 0:
                        owed[rid] -= 1
                        anchored[key][fb["label"]] += 1
                    else:
                        anchored[key]["unanchored"] += 1
                rendered = canon(payload)[:MATCH_TARGET_CHARS]
                for key, rx in compiled.items():
                    if rx.search(rendered):
                        matches[key][routine] += 1
    utils_dir = library / "utils"
    for r in rems:
        key = (r["routine"], r["id"])
        hits = matches.get(key, {})
        r["own"] = hits.get(r["routine"], 0) if r["routine"] else 0
        r["others"] = {k: v for k, v in sorted(hits.items()) if k != r["routine"]}
        r["anchored"] = anchored.get(key, dict.fromkeys(("holds", "unanchored", *LABELS), 0))
        problems = r.setdefault("problems", [])
        if key in compiled and not hits:
            problems.append("matches no retained action anywhere")
        if m := _UTIL_ANCHOR.match(r["regex"]):
            util = utils_dir / m[1]
            if utils_dir.is_dir() and not util.is_dir():
                problems.append(f"anchored on util {m[1]}, which the library no longer holds")
            elif util.is_dir() and _revised_after(util, r["created_run"]):
                problems.append(f"util {m[1]} was revised after this reminder was written — "
                                "re-check its premise")
    return {"reminders": rems, "clusters": clusters(rems),
            "summary": {"reminders": len(rems),
                        "local": sum(r["scope"] == "local" for r in rems),
                        "global": sum(r["scope"] == "global" for r in rems),
                        "with_problems": sum(bool(r["problems"]) for r in rems)}}


def _revised_after(util: Path, created_run: str) -> bool:
    """Whether any file of `util` changed after the run that wrote the reminder. A run id's
    stamp is UTC (`rsched.ids.run_ts`) and is read as UTC: read as LOCAL time it moved by the
    host's offset, so on a host east of UTC a util last touched before the reminder was written
    — up to two hours before, in a Berlin summer — was reported as revised after it."""
    m = re.search(r"(\d{8})-(\d{6})", created_run or "")
    if not m:
        return False
    created = datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc).timestamp()
    try:
        return max(p.stat().st_mtime for p in util.rglob("*") if p.is_file()) > created
    except (OSError, ValueError):
        return False


def clusters(rems: list[dict]) -> list[dict]:
    """Reminders of DIFFERENT routines on one util anchor, or matching the same routines'
    actions — the same caution written more than once."""
    by_anchor: dict[str, list[dict]] = defaultdict(list)
    for r in rems:
        if m := _UTIL_ANCHOR.match(r["regex"]):
            by_anchor[m[1]].append(r)
    out = []
    for util, members in sorted(by_anchor.items()):
        routines = {r["routine"] or "(global)" for r in members}
        if len(routines) > 1:
            out.append({"anchor": f"util:{util}", "routines": sorted(routines),
                        "reminders": [f"{r['routine'] or 'global'}/{r['id']}" for r in members]})
    return out


def _table(result: dict) -> str:
    lines = [f"{s}: {n}" for s, n in result["summary"].items()]
    for r in result["reminders"]:
        a = r["anchored"]
        lines.append(f"{r['routine'] or 'global'}/{r['id']} /{r['regex']}/ holds={a['holds']} "
                     + " ".join(f"{k}={a[k]}" for k in (*LABELS, "unanchored"))
                     + f" own={r['own']} others={sum(r['others'].values())}"
                     + (f" PROBLEMS: {'; '.join(r['problems'])}" if r["problems"] else ""))
    lines += [f"cluster {c['anchor']}: {', '.join(c['reminders'])}" for c in result["clusters"]]
    return "\n".join(lines)


def selftest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home, lib = Path(tmp) / "routines", Path(tmp) / "library"
        (lib / "utils" / "fs-ops").mkdir(parents=True)
        (lib / "reminders").mkdir()
        for slug, regex in (("a", "^util:fs-ops mv "), ("b", "^util:fs-ops mv .*--force"),
                            ("c", "^util:gone ")):
            (home / slug / "state").mkdir(parents=True)
            (home / slug / "state" / "reminders.json").write_text(json.dumps({"reminders": [
                {"id": f"rem-{slug}", "regex": regex, "description": "d",
                 "created_run": f"{slug}:20990101-000000"}]}))
        run = home / "a" / "runs" / "20260901-000000"
        run.mkdir(parents=True)
        events = [
            {"type": "assistant_action", "payload": {"kind": "util", "name": "fs-ops",
                                                     "args": ["mv", "x", "y"]}},
            {"type": "observation", "payload": {"kind": "reminder_hold", "reminders": [
                {"id": "rem-a", "scope": "local"}]}},
            {"type": "assistant_action", "payload": {
                "kind": "write_file", "path": "p",
                "remind_feedback": {"id": "rem-a", "label": "would_have"}}},
            {"type": "assistant_action", "payload": {
                "kind": "write_file", "path": "q",
                "remind_feedback": {"id": "rem-a", "label": "would_have"}}}]
        (run / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        other = home / "b" / "runs" / "20260901-000000"
        other.mkdir(parents=True)
        with gzip.open(other / "transcript.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant_action", "payload": {
                "kind": "util", "name": "fs-ops", "args": ["mv", "a", "b"]}}) + "\n")
        # a child run's own child sits under ITS sub/ — two levels down
        grandchild = other / "sub" / "1" / "sub" / "2"
        grandchild.mkdir(parents=True)
        (grandchild / "transcript.jsonl").write_text(json.dumps(
            {"type": "assistant_action", "payload": {
                "kind": "util", "name": "fs-ops", "args": ["mv", "c", "d"]}}) + "\n")
        result = census(home, lib)
        by_id = {r["id"]: r for r in result["reminders"]}
        a = by_id["rem-a"]
        assert a["anchored"]["holds"] == 1 and a["anchored"]["would_have"] == 1, a
        assert a["anchored"]["unanchored"] == 1, a                     # one label per hold
        # the gz run is read, and so is the grandchild run under it
        assert a["own"] == 1 and a["others"] == {"b": 2}, a
        assert "matches no retained action anywhere" in by_id["rem-b"]["problems"]
        assert any("no longer holds" in p for p in by_id["rem-c"]["problems"])
        assert result["clusters"] and result["clusters"][0]["anchor"] == "util:fs-ops"
        assert canon({"kind": "script", "name": "store", "args": ["stage"]}) == "script:store stage"
        assert canon({"kind": "read_file", "paths": ["a", "b"]}) == "read_file paths=a,b"
        # a run stamp is UTC: a util last touched 30 min BEFORE the reminder was written is not
        # "revised after" it on a host east of UTC (POSIX `Etc/GMT-2` is UTC+2) — read as local
        # time, the stamp moved two hours back and the census said exactly that
        util = lib / "utils" / "fs-ops"
        (util / "main.py").write_text("x\n")
        written = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).timestamp()
        saved_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Etc/GMT-2"
        time.tzset()
        try:
            os.utime(util / "main.py", (written - 1800, written - 1800))
            assert not _revised_after(util, "a:20260901-120000"), "a UTC stamp read as local"
            os.utime(util / "main.py", (written + 1800, written + 1800))
            assert _revised_after(util, "a:20260901-120000"), "a later revision is still caught"
        finally:
            if saved_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved_tz
            time.tzset()
    print("selftest: ok", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(prog="gu reminder-census")
    ap.add_argument("--routines-home", default=str(Path.home() / "routines"))
    ap.add_argument("--library", default=str(Path.home() / ".local/share/"
                                             "routine-scheduler-libraries"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    routines, library = Path(args.routines_home).expanduser(), Path(args.library).expanduser()
    if not routines.is_dir():
        print(f"reminder-census: no routines home at {routines}", file=sys.stderr)
        return 1
    result = census(routines, library)
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else _table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
