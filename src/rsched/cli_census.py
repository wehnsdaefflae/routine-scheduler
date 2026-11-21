"""`rsched census` — what this instance actually USES, so disuse is visible before anything is
added to a feature. Over the retained transcripts (routines and conversations) and every
`routine.yaml` (routines, conversations and detached background tasks — a conversation's `kind`
and `playbook` and a task's `owner` live only there) it counts action kinds landed, side fields
used, top-level config keys, `capabilities` values and health events written — each with its
count and the number of distinct subjects — and lists every entry of each known vocabulary at
zero. Read-only.

An ALIAS kind (`websearch`, `code_search` — engine/kindaliases.py) lands as the util call it
spells, so it is counted from the event's `spelled` mark, IN ADDITION to that util call: the
`util` count is every util call however it was written.
"""

from __future__ import annotations

import gzip
import json
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from .config import load_server_config
from .config.routine import TOP_KEYS
from .engine.actions import SIDE_FIELDS
from .engine.actionschema import KINDS
from .health_events import HEALTH_EVENTS_FILE
from .health_vocab import EVENTS
from .paths import read_jsonl, read_yaml


class _Tally:
    def __init__(self) -> None:
        self.count: Counter[str] = Counter()
        self.where: defaultdict[str, set[str]] = defaultdict(set)

    def add(self, name: str, subject: str) -> None:
        self.count[name] += 1
        self.where[name].add(subject)

    def section(self, known: tuple[str, ...] | frozenset[str] | None) -> dict:
        used = [{"name": n, "count": c, "subjects": len(self.where[n])}
                for n, c in self.count.most_common()]
        return {"used": used, **({} if known is None else
                                 {"never": sorted(set(known) - set(self.count))})}


def _subjects(home: Path) -> Iterator[Path]:
    if home.is_dir():
        yield from sorted(p for p in home.iterdir() if p.is_dir() and not p.name.startswith("."))


def _actions(path: Path, since: str) -> Iterator[dict]:
    """Every landed `assistant_action` event in one transcript (plain or rotated) from `since`
    on — the whole event, since the alias mark rides beside the payload.
    """
    try:
        with (gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")) as fh:
            for raw in fh:
                if b'"assistant_action"' not in raw:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if (isinstance(ev, dict) and ev.get("type") == "assistant_action"
                        and str(ev.get("ts", "")) >= since and isinstance(ev.get("payload"), dict)):
                    yield ev
    except (OSError, EOFError):
        return


def _scan_transcripts(server, since: str, days: int, kinds: _Tally, sides: _Tally) -> int:
    oldest_mtime, transcripts = time.time() - (days + 1) * 86400, 0
    for home in (server.routines_home, server.conversations_home):
        for subject in _subjects(home):
            for path in sorted((subject / "runs").rglob("transcript.jsonl*")):
                if path.suffix not in {".jsonl", ".gz"} or path.stat().st_mtime < oldest_mtime:
                    continue
                transcripts += 1
                for event in _actions(path, since):
                    action = event["payload"]
                    kinds.add(str(action.get("kind")), subject.name)
                    if spelled := event.get("spelled"):
                        kinds.add(str(spelled), subject.name)
                    for field in (f for f in SIDE_FIELDS if action.get(f)):
                        sides.add(field, subject.name)
    return transcripts


def _scan_configs(server, keys: _Tally, caps: _Tally) -> int:
    """Count every config's top-level keys and capability values; returns how many were read."""
    configs = 0
    for home in (server.routines_home, server.conversations_home, server.background_home):
        for subject in _subjects(home):
            try:
                raw = read_yaml(subject / "routine.yaml", {})
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(raw, dict) or not raw:
                continue
            configs += 1
            for key in raw:
                keys.add(str(key), subject.name)
            held = raw.get("capabilities")
            for cap, value in (held.items() if isinstance(held, dict) else ()):
                for one in value if isinstance(value, list) else [value]:
                    caps.add(f"{cap}={one}" if isinstance(one, str | int | bool) else cap,
                             subject.name)
    return configs


def census(server, days: int) -> dict:
    since = (datetime.now(tz=UTC) - timedelta(days=days)).date().isoformat()
    kinds, sides, keys, caps, events = _Tally(), _Tally(), _Tally(), _Tally(), _Tally()
    transcripts = _scan_transcripts(server, since, days, kinds, sides)
    configs = _scan_configs(server, keys, caps)
    for row in read_jsonl(server.routines_home / ".control" / HEALTH_EVENTS_FILE):
        if str(row.get("ts", "")) >= since:
            events.add(str(row.get("event")), str(row.get("routine", "")))
    return {"days": days, "since": since, "transcripts": transcripts, "configs": configs,
            "action_kinds": kinds.section(KINDS), "side_fields": sides.section(SIDE_FIELDS),
            "config_keys": keys.section(TOP_KEYS), "capabilities": caps.section(None),
            "health_events": events.section(EVENTS)}


def cmd_census(args) -> int:
    server, problems = load_server_config()
    for line in problems:
        print(f"server config: {line}")
    doc = census(server, args.days)
    if args.json:
        print(json.dumps(doc, indent=2))
        return 0
    print(f"census since {doc['since']} ({doc['days']} days, {doc['transcripts']} transcripts, "
          f"{doc['configs']} configs) — count · routines/conversations/tasks · name")
    for title in ("action_kinds", "side_fields", "config_keys", "capabilities", "health_events"):
        section = doc[title]
        print(f"\n{title}:")
        for row in section["used"]:
            print(f"  {row['count']:>8}  {row['subjects']:>4}  {row['name']}")
        if "never" in section:
            print(f"  never used: {', '.join(section['never']) or '—'}")
    return 0


def add_parser(sub) -> None:
    """Register `census` on the CLI's subparsers — called from `cli.main`."""
    ce = sub.add_parser("census", help="what this instance uses — and never uses — of its "
                                       "action kinds, side fields, config keys and health events")
    ce.add_argument("--days", type=int, default=60, help="window in days (default 60)")
    ce.add_argument("--json", action="store_true", help="one JSON document instead of lines")
    ce.set_defaults(fn=cmd_census)
