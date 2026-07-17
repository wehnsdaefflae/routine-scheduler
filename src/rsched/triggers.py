"""Event triggers — the `triggers:` config shape + the durable event spool.

A trigger fires a routine on an EXTERNAL EVENT, alongside cron: routine.yaml grows one
canonical `triggers:` list (user config — created/deleted on the routine page, never by a
run), each entry `{id, type, ...}` carrying its type's own keys. `webhook`
(POST /api/hooks/<slug>/<token>) is the implemented type; `imap` (mail arrival) and
`watch_path` (file drop) are reserved names in the SAME shape so they slot in later
without reshaping anything — their watchers will drop the same spool events a webhook
does and everything downstream is already type-agnostic.

Ownership mirrors restart.request and the background .requests/ idiom: the WEB layer
(api_hooks) only RECORDS events — one JSON file per event under the spool
(`<routines_home>/.control/triggers/<slug>/evt-*.json`, atomic) — and the DAEMON's
TriggerManager (daemon/triggers.py) turns them into fires on the scheduler tick, so run
spawning, one-run-per-routine and the slot pools stay the daemon's job. `state.json`
next to the events is the daemon-written fire ledger (last-fired stamps + counters) the
routine page renders. Full semantics: docs/triggers.md.

This module is the shared vocabulary both sides import: config validation (called from
config.load_routine), token/id generation, and the spool file IO.
"""

from __future__ import annotations

import secrets
import uuid
from collections import Counter
from pathlib import Path

from .ids import now_iso, run_ts
from .paths import atomic_write_json, read_json

TRIGGER_TYPES = ("webhook", "imap", "watch_path")
# Minimum seconds between trigger-initiated fires of one routine — the budget backstop
# that makes a leaked hook URL boring: however hard it is hammered, at most one run per
# window, everything else coalesces (docs/triggers.md § Coalescing).
DEFAULT_COOLDOWN_S = 60
MAX_PAYLOAD_BYTES = 64 * 1024   # a webhook body past this is rejected 413, never stored
MAX_PENDING_EVENTS = 32         # spool cap per routine — past it new events are rejected 429


def new_webhook_trigger(*, cooldown_s: int = DEFAULT_COOLDOWN_S) -> dict:
    """A fresh webhook trigger entry, ready to append to routine.yaml `triggers:`. The
    id and URL token are always server-generated — the token IS the hook's auth (the
    route takes no bearer), so it is never client-supplied.
    """
    return {"id": f"t-{uuid.uuid4().hex[:8]}", "type": "webhook",
            "token": secrets.token_urlsafe(24),
            "cooldown_s": int(cooldown_s), "created": now_iso()}


def hook_path(slug: str, trigger: dict) -> str:
    """The URL path a third party POSTs to (the UI prefixes its own origin)."""
    return f"/api/hooks/{slug}/{trigger.get('token', '')}"


def validate_triggers(raw: object) -> tuple[list[dict], list[str]]:
    """Canonicalize a routine.yaml `triggers:` value. Returns (entries, problems):
    invalid entries are reported and DROPPED (fail closed — a malformed webhook must
    never be token-matchable), valid ones pass through verbatim so per-type keys and
    future fields survive. Reserved-but-unimplemented types are kept, with a problem
    line saying nothing fires them yet.
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["triggers: expected a list"]
    out: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"triggers[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        entry = dict(item)
        ttype = str(entry.get("type") or "")
        tid = str(entry.get("id") or "")
        if ttype not in TRIGGER_TYPES:
            problems.append(f"{where}: unknown type {ttype!r} "
                            f"(expected one of {TRIGGER_TYPES})")
            continue
        if not tid:
            problems.append(f"{where}: missing id")
            continue
        if tid in seen:
            problems.append(f"{where}: duplicate id {tid!r}")
            continue
        cooldown = entry.get("cooldown_s", DEFAULT_COOLDOWN_S)
        if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
            problems.append(f"{where}: cooldown_s must be a non-negative integer "
                            f"(got {cooldown!r}; using {DEFAULT_COOLDOWN_S})")
            cooldown = DEFAULT_COOLDOWN_S
        entry["cooldown_s"] = cooldown
        if ttype == "webhook" and not str(entry.get("token") or "").strip():
            problems.append(f"{where}: webhook trigger without a token — dropped "
                            "(recreate it on the routine page)")
            continue
        if ttype != "webhook":
            problems.append(f"{where}: trigger type {ttype!r} is reserved but not "
                            "implemented yet — the entry is kept, nothing fires it")
        seen.add(tid)
        out.append(entry)
    return out, problems


# -- the event spool --------------------------------------------------------------------
# Web writes evt-*.json (one per accepted event), the daemon consumes them and writes
# state.json. Lives under the .control dot-dir the registry scan ignores.


def spool_dir(routines_home: Path, slug: str) -> Path:
    return routines_home / ".control" / "triggers" / slug


def write_event(routines_home: Path, slug: str, *, trigger_id: str, payload: str,
                content_type: str = "", client: str = "") -> Path:
    """Record one trigger event durably (atomic). The filename sorts chronologically;
    the random suffix keeps same-second events distinct.
    """
    name = f"evt-{run_ts()}-{uuid.uuid4().hex[:6]}.json"
    return atomic_write_json(spool_dir(routines_home, slug) / name,
                             {"trigger": trigger_id, "ts": now_iso(), "payload": payload,
                              "content_type": content_type, "client": client})


def pending_events(routines_home: Path, slug: str) -> list[Path]:
    """Unconsumed event files, oldest first."""
    d = spool_dir(routines_home, slug)
    return sorted(d.glob("evt-*.json")) if d.is_dir() else []


def slugs_with_events(routines_home: Path) -> list[str]:
    root = routines_home / ".control" / "triggers"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(d.glob("evt-*.json")))


def read_state(routines_home: Path, slug: str) -> dict:
    """The daemon-maintained fire ledger: {last_fired, fires, triggers: {id: {…}}}."""
    st = read_json(spool_dir(routines_home, slug) / "state.json")
    return st if isinstance(st, dict) else {}


def write_state(routines_home: Path, slug: str, state: dict) -> None:
    atomic_write_json(spool_dir(routines_home, slug) / "state.json", state)


def describe_triggers(routines_home: Path, slug: str, entries: list[dict]) -> list[dict]:
    """The routine page's Triggers rows: config + the fire ledger + the live pending
    count. The webhook URL path (token included) rides along — the detail API is
    bearer-authed, so the operator seeing the secret is the point (copy button).
    """
    if not entries:
        return []
    state = read_state(routines_home, slug)
    raw_per = state.get("triggers")
    per: dict = raw_per if isinstance(raw_per, dict) else {}
    counts: Counter[str] = Counter()
    for p in pending_events(routines_home, slug):
        ev = read_json(p)
        if isinstance(ev, dict):
            counts[str(ev.get("trigger"))] += 1
    rows = []
    for t in entries:
        tid = str(t.get("id"))
        got = per.get(tid)
        mine: dict = got if isinstance(got, dict) else {}
        rows.append({"id": tid, "type": str(t.get("type")),
                     "cooldown_s": int(t.get("cooldown_s") or 0),
                     "created": str(t.get("created") or ""),
                     "url_path": hook_path(slug, t) if t.get("type") == "webhook" else "",
                     "last_fired": str(mine.get("last_fired") or ""),
                     "events": int(mine.get("events") or 0),
                     "pending": counts.get(tid, 0)})
    return rows
