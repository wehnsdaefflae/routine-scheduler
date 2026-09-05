"""Consequence REMINDERS — the store behind the just-in-time caution layer.

A reminder is `(regex → consequence)`: a pattern over the canonical one-line rendering of an
action (`engine/actionschema.canon`) plus the short caution that pattern is worth interrupting
for. Before an action executes, the engine tests it against every live reminder and HOLDS the
matching ones (`engine/remind.py`) so the model re-decides with the caution in front of it.

This file owns only the STORE — what a reminder IS, where the two of them live, how the union
is formed, and how the four-way outcome tally accumulates. The interception, the authoring ops
and the prompt wording live engine-side.

Two stores, by BLAST RADIUS:

- **local** — `<routine>/state/reminders.json`, this routine's own runtime state (like
  `state/notes.md`). A bad local reminder taxes one routine's turns, so authoring it is
  autonomous.
- **global** — `<libraries_home>/reminders/<id>.json`, one file per reminder beside `rules/`
  and `permissions/`, riding the same git sync. A bad global reminder taxes EVERY capable
  routine at its next run, silently — so a global write is approval-gated
  (`capabilities.remind_confirm`), exactly as a rule revision is.

The STATS are per-routine on purpose and live only in the local file — for a global reminder
too, under `global_stats`. A global reminder's DEFINITION is curated and shared; the evidence
about it is local, because "did this fire uselessly" is a question about one routine's work.
Keeping the tally out of the library also keeps the library from taking a git commit on every
fire, from every routine, concurrently.

Precedence when both stores are active: the union, deduped by REGEX with local winning. Same
regex = the same match class, which is the only "same consequence" test a machine can make;
different regexes are different classes and both are shown (in ONE hold — the engine never
multiplies turns by the number of matches).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import atomic_write_json, read_json

LOCAL_FILE = "reminders.json"       # under <routine>/state/
GLOBAL_DIR = "reminders"            # under libraries_home
SCOPES = ("local", "global")
#: The capability dial, least → most reach. `global` means BOTH stores (the union), not the
#: library store alone: a routine curating shared cautions still keeps its own.
LEVELS = ("none", "local", "global")
LEVEL_RANK = {level: n for n, level in enumerate(LEVELS)}
#: The four-way outcome label (the confusion matrix that tunes a regex). `fires` is the
#: denominator; `fires - Σlabels` is how many holds the model left unlabelled.
LABELS = ("could_not", "would_have", "did", "didnt")
STAT_FIELDS = ("fires", *LABELS)
#: How the four labels are put to the model. Beside the labels they gloss, because both the
#: hold's wording (engine/observations) and the unlabelled-fire nudge (engine/remind) have to
#: say the same thing — a label picked wrongly is worse evidence than no label at all.
LABEL_HELP = ("The labels: could_not (the consequence was impossible for THIS action — the "
              "pattern is too broad) · would_have (it was on track and you are now avoiding "
              "it) · did (you went ahead and it happened) · didnt (you went ahead and nothing "
              "bad happened). Without them the pattern cannot be tuned and the reminder "
              "cannot earn its turns.")

#: A reminder id becomes a FILENAME in the library store, so it is validated wherever one
#: arrives from outside this module — the model names an id in a revise/delete op, and a
#: global record carries its own `id` field, which a git sync or a hand-edit can set to
#: anything. `write_util` / `memory_write` / `schedule_run` all slug-check names that become
#: paths for the same reason; the library is a multi-writer git repo, so "the engine is the
#: only writer" is not a defence here.
ID_RE = re.compile(r"^rem-[A-Za-z0-9][A-Za-z0-9-]{0,63}$")

MAX_REGEX_CHARS = 200
MAX_DESCRIPTION_CHARS = 400
#: A runaway backstop on the local store, not a quota: every live reminder is tested against
#: every action, so an unbounded store would tax every turn of every run forever.
MAX_LOCAL = 40
#: The match target is truncated before matching — a bounded subject is the only cheap defence
#: against a pathological model-authored pattern (Python's `re` has no timeout). Far above any
#: real action string, so it changes nothing a regex can legitimately see.
MATCH_TARGET_CHARS = 2_000


def blank_stats() -> dict:
    return dict.fromkeys(STAT_FIELDS, 0)


def is_reminder_id(rid: object) -> bool:
    """Is this a reminder id safe to use as a filename? (see ID_RE)"""
    return isinstance(rid, str) and bool(ID_RE.match(rid))


@dataclass(frozen=True)
class Reminder:
    """One live reminder, as the engine reads it: the definition plus THIS routine's tally."""

    id: str
    regex: str
    description: str
    scope: str
    created_run: str
    stats: dict

    def matches(self, canon: str) -> bool:
        """Does this reminder's pattern fire on that canonical action string?

        `re.search`, so a pattern says where it anchors (`^util:fs-ops mv `) instead of having
        to describe the whole line. A pattern that no longer compiles never fires — the store
        must not be able to break a run, and the write gate already rejected it once.
        """
        try:
            return bool(re.search(self.regex, canon[:MATCH_TARGET_CHARS]))
        except re.error:
            return False

    def as_record(self) -> dict:
        return {"id": self.id, "regex": self.regex, "description": self.description,
                "scope": self.scope, "created_run": self.created_run, "stats": dict(self.stats)}


def regex_problem(pattern: object) -> str | None:
    """Why this pattern may not be stored — or None when it is usable.

    Checked at the WRITE gate (inside the schema-retry cycle) so a malformed pattern is
    corrected before it becomes a turn, never silently dropped afterwards.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        return "remind.regex must be a non-empty pattern over the canonical action string"
    if len(pattern) > MAX_REGEX_CHARS:
        return (f"remind.regex is {len(pattern)} characters — at most {MAX_REGEX_CHARS}; "
                "a pattern that long is matching a whole command, not a class of them")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return f"remind.regex is not a valid regular expression ({exc})"
    if compiled.search(""):
        return ("remind.regex matches the EMPTY string, so it would hold every action you "
                'take — anchor it to the action class you mean (e.g. "^util:fs-ops mv ")')
    return None


def description_problem(text: object) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return ("remind.description must say what the consequence IS — the caution is what "
                "the hold shows you")
    if len(text) > MAX_DESCRIPTION_CHARS:
        return (f"remind.description is {len(text)} characters — at most "
                f"{MAX_DESCRIPTION_CHARS}; one or two sentences")
    return None


def new_id(run_ts: str, taken: set[str]) -> str:
    """`rem-<run-ts>-<n>` — stable, sortable, and unique against ids already in the store."""
    n = 1
    while f"rem-{run_ts}-{n}" in taken:
        n += 1
    return f"rem-{run_ts}-{n}"


# --- the local store ---------------------------------------------------------------------

def local_path(routine_dir: Path) -> Path:
    return Path(routine_dir) / "state" / LOCAL_FILE


def _stats(raw: object) -> dict:
    got = raw if isinstance(raw, dict) else {}
    return {f: int(got.get(f) or 0) for f in STAT_FIELDS}


def load_local(routine_dir: Path) -> tuple[list[Reminder], dict[str, dict]]:
    """This routine's own reminders, plus its tally about GLOBAL ones. Lenient: a hand-broken
    file reads as an empty store rather than failing a run at boot.
    """
    raw = read_json(local_path(routine_dir), {})
    if not isinstance(raw, dict):
        return [], {}
    out = []
    for rec in raw.get("reminders") or []:
        if not isinstance(rec, dict) or not rec.get("id") or not rec.get("regex"):
            continue
        out.append(Reminder(id=str(rec["id"]), regex=str(rec["regex"]),
                            description=str(rec.get("description") or ""), scope="local",
                            created_run=str(rec.get("created_run") or ""),
                            stats=_stats(rec.get("stats"))))
    gstats = {str(k): _stats(v) for k, v in (raw.get("global_stats") or {}).items()
              if isinstance(v, dict)}
    return out, gstats


def save_local(routine_dir: Path, reminders: list[Reminder],
               global_stats: dict[str, dict]) -> None:
    atomic_write_json(local_path(routine_dir), {
        "reminders": [{k: v for k, v in r.as_record().items() if k != "scope"}
                      for r in reminders if r.scope == "local"],
        "global_stats": {k: _stats(v) for k, v in sorted(global_stats.items())}})


# --- the global (library) store -----------------------------------------------------------

def global_path(reminders_home: Path, rid: str) -> Path:
    """The file one curated reminder lives in. Raises on an id that is not a plain reminder
    id — an id is a path segment, and `..` in one would reach outside the library entirely.
    """
    if not is_reminder_id(rid):
        raise ValueError(f"not a reminder id: {rid!r}")
    return Path(reminders_home) / f"{rid}.json"


def load_global(reminders_home: Path, stats: dict[str, dict] | None = None) -> list[Reminder]:
    """Every curated reminder in the library, carrying THIS routine's tally about each."""
    home = Path(reminders_home)
    if not home.is_dir():
        return []
    tally = stats or {}
    out = []
    for path in sorted(home.glob("*.json")):
        rec = read_json(path, {})
        if not isinstance(rec, dict) or not rec.get("regex"):
            continue
        rid = str(rec.get("id") or path.stem)
        if not is_reminder_id(rid):
            continue        # a record whose id could not be written back is not usable
        out.append(Reminder(id=rid, regex=str(rec["regex"]),
                            description=str(rec.get("description") or ""), scope="global",
                            created_run=str(rec.get("created_run") or ""),
                            stats=_stats(tally.get(rid))))
    return out


def write_global(reminders_home: Path, reminder: Reminder) -> Path:
    """Install/replace one curated reminder. The library's copy carries NO stats — the tally
    is each holder's own (see the module docstring).
    """
    path = global_path(reminders_home, reminder.id)
    rec = reminder.as_record()
    atomic_write_json(path, {k: rec[k] for k in ("id", "regex", "description", "created_run")})
    return path


def delete_global(reminders_home: Path, rid: str) -> bool:
    try:
        global_path(reminders_home, rid).unlink()
    except (OSError, ValueError):
        return False
    return True


def global_rel(rid: str) -> str:
    """The library-repo-relative path of one global reminder — what a commit stages."""
    if not is_reminder_id(rid):
        raise ValueError(f"not a reminder id: {rid!r}")
    return f"{GLOBAL_DIR}/{rid}.json"


# --- the union the engine matches against -------------------------------------------------

def active(routine_dir: Path, reminders_home: Path, level: str) -> list[Reminder]:
    """The live set for one run: local, global, or the union with LOCAL OVERRIDING GLOBAL.

    Dedupe is by regex — the only "same consequence class" test available to a machine, and
    the same one the authoring heuristic implies (the match target signals the scope).
    """
    if LEVEL_RANK.get(level, 0) < LEVEL_RANK["local"]:
        return []
    local, gstats = load_local(routine_dir)
    if LEVEL_RANK.get(level, 0) < LEVEL_RANK["global"]:
        return local
    seen = {r.regex for r in local}
    return local + [g for g in load_global(reminders_home, gstats) if g.regex not in seen]


def matching(reminders: list[Reminder], canon: str) -> list[Reminder]:
    return [r for r in reminders if r.matches(canon)]


def record(routine_dir: Path, reminder: Reminder, field: str) -> dict:
    """Increment one tally field (`fires` or an outcome LABEL) and return the tally AFTER it.

    THE ONLY WRITER OF A TALLY, and it works off DISK, not off the caller's copy: a
    read-modify-write of the local file, whether the reminder is local or global. One routine
    is one writer and a run takes at most one turn at a time, so that is enough. The caller
    gets the new tally back because the engine's in-memory set holds frozen `Reminder`s that
    would otherwise never learn their own fire was counted — and would then write the stale
    count back over this one (`engine/remind._save_local` keeps the two halves apart).

    Best-effort — a failed tally must never fail the turn that produced it, so a failure
    returns the tally the caller already had.
    """
    if field not in STAT_FIELDS:
        return dict(reminder.stats)
    try:
        local, gstats = load_local(routine_dir)
        if reminder.scope == "global":
            stats = gstats.setdefault(reminder.id, blank_stats())
            stats[field] = int(stats.get(field) or 0) + 1
        else:
            found: dict | None = None
            rebuilt = []
            for r in local:
                if r.id != reminder.id:
                    rebuilt.append(r)
                    continue
                found = {**r.stats, field: int(r.stats.get(field) or 0) + 1}
                rebuilt.append(Reminder(**{**r.as_record(), "scope": "local", "stats": found}))
            if found is None:      # gone from disk (deleted by an earlier op this run)
                return dict(reminder.stats)
            local, stats = rebuilt, found
        save_local(routine_dir, local, gstats)
    except (OSError, ValueError):
        return dict(reminder.stats)
    return dict(stats)


def find(reminders: list[Reminder], rid: str) -> Reminder | None:
    return next((r for r in reminders if r.id == rid), None)
