"""Consequence REMINDERS — the store behind the just-in-time caution layer.

A reminder is `(trigger → consequence)`: a pattern, a KIND saying what that pattern is tested
against, and the short caution the match is worth interrupting for.

The original and default kind is `action` — the canonical one-line rendering of an action
(`engine/actionschema.canon`), tested BEFORE the action executes, so the engine HOLDS it
(`engine/remind.py`) and the model re-decides with the caution in front of it. A caution that
arrives afterwards arrives after the consequence, which is why that kind holds and the others
cannot.

The other kinds (`KINDS`, decided as D152 option C) watch moments nothing could watch before:
`result` the observation that came back, `prose` the turn's own `say`, and `turn:first` /
`turn:finish` / `turn:question` / `turn:answer` / `turn:error` the special turns. All of them
describe something that has already happened, so they INFORM at no turn cost — the caution
rides the observation tail the way an engine note does — and never hold.

This file owns only the STORE — what a reminder IS, where the two of them live, how the union
is formed, and how the four-way outcome tally accumulates. The interception, the authoring ops
and the prompt wording live engine-side.

Two stores, by BLAST RADIUS:

- **local** — `<routine>/state/reminders.json`, this routine's own runtime state (like
  `state/notes.md`). A bad local reminder taxes one routine's turns, so authoring it is
  autonomous.
- **global** — `<libraries_home>/reminders/<id>.json`, one file per reminder beside `rules/`
  and `permissions/`, riding the same git sync. A bad global reminder taxes every routine it
  reaches at its next run, silently — so a global write is approval-gated
  (`capabilities.remind_confirm`), exactly as a rule revision is.

A global reminder declares its REACH. `universal`: a consequence any caller of that util or
action meets, held for every routine whose action matches — its anchor limits it, since a
routine that never makes the call never pays. `listed`: a caution that belongs to a kind of
work (a git push in a published repo, a job on a shared GPU), held only for the routines whose
settings list it (`shared_reminders`, which a settings pattern carries for its kind of work).

The dial (`capabilities.reminders`) governs AUTHORING and nothing else: `none` switches the
layer off; `local` applies this routine's own reminders plus every curated one that reaches it —
and lets it write its own; `global` also lets it write the shared store — the curator's setting.
Reading a curated reminder needs no dial of its own: the operator approved every one of them.

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
from typing import Any

from .paths import atomic_write_json, read_json

LOCAL_FILE = "reminders.json"       # under <routine>/state/
GLOBAL_DIR = "reminders"            # under libraries_home
SCOPES = ("local", "global")
#: What a reminder WATCHES — its trigger. `action` is the original and the default: the
#: canonical one-line rendering of an action, tested BEFORE it runs, so a hold can still
#: prevent the consequence. The others are moments nothing could watch before (D152, option C):
#:
#: - `result`   — the OBSERVATION that came back. Knowable only after the action ran, so it
#:                never holds; the caution rides the observation at no turn cost.
#: - `prose`    — the turn's own `say`. What the run SAID it was doing, which is where a
#:                drift ("I will force push") is visible before the call that does it.
#: - `turn:*`   — the special turns: `first` (the opening turn of a run), `finish`,
#:                `question` (an ask_user), `answer` (a user reply arriving), `error` (an
#:                observation reporting failure).
#:
#: A record whose kind is unreadable reads as `action`: the store must never be able to break
#: a run, and `action` is the one kind every pre-change record meant.
TURN_MOMENTS = ("first", "finish", "question", "answer", "error")
KINDS = ("action", "result", "prose", *(f"turn:{m}" for m in TURN_MOMENTS))
DEFAULT_KIND = "action"
#: The kinds that are tested BEFORE the action runs and can therefore HOLD it. Every other
#: kind describes something that has already happened, so it can only inform.
HOLDING_KINDS = ("action",)
#: The authoring dial, least → most reach (see the module docstring): `global` adds writing the
#: shared store; a routine curating shared cautions still keeps its own.
LEVELS = ("none", "local", "global")
#: How far a curated reminder reaches (see the module docstring).
REACHES = ("universal", "listed")
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

#: A runaway backstop on the local store, not a quota: every live reminder is tested against
#: every action, so an unbounded store would tax every turn of every run forever.
MAX_LOCAL = 40
#: The match target is truncated before matching — a bounded subject is the only cheap defence
#: against a pathological model-authored pattern (Python's `re` has no timeout). Far above any
#: real action string, so it changes nothing a regex can legitimately see.
MATCH_TARGET_CHARS = 2_000


#: How many fires before a reminder's own tally is evidence about its pattern rather than
#: noise. Five: enough that a plurality means something, few enough that a bad pattern is
#: caught inside one routine's week rather than after a hundred wasted turns.
PRUNE_MIN_FIRES = 5


def looks_too_broad(stats: dict) -> bool:
    """Does this reminder's OWN tally say its pattern fires where the consequence cannot apply?

    `could_not` is the one label that indicts the pattern: the consequence was impossible for
    the action that was held, so the turn bought nothing and would buy nothing next time. The
    test is a plurality over enough fires, never a majority over all four — `would_have` and
    `didnt` both describe a working reminder and would otherwise drown the signal.

    This is the tally's ONLY automatic consumer, and it decides nothing: it selects which holds
    show the model its own evidence, in the hold it was already paying for. Nothing demotes or
    deletes a reminder behind the run's back — 21 days of live holds carried 67 `could_not`
    labels that no code anywhere read, and the fix for that is to put them where the decision
    is made, not to move the decision.
    """
    fires = int(stats.get("fires") or 0)
    if fires < PRUNE_MIN_FIRES:
        return False
    could_not = int(stats.get("could_not") or 0)
    return could_not > max(int(stats.get(lbl) or 0) for lbl in LABELS if lbl != "could_not")


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
    reach: str = ""          # a global reminder's REACHES entry; "" for a local one
    kind: str = DEFAULT_KIND  # what it WATCHES (see KINDS); "action" for every older record

    def matches(self, canon: str) -> bool:
        """Does this reminder's pattern fire on that match target?

        `re.search`, so a pattern says where it anchors (`^util:fs-ops mv `) instead of having
        to describe the whole line. A pattern that no longer compiles never fires — the store
        must not be able to break a run, and the write gate already rejected it once.

        The TARGET depends on `kind` and is chosen by the caller (`matching`): the canonical
        action string, an observation's rendering, the turn's prose. This method only applies
        the pattern — it cannot know which moment it was asked at.
        """
        try:
            return bool(re.search(self.regex, canon[:MATCH_TARGET_CHARS]))
        except re.error:
            return False

    def as_record(self) -> dict:
        return {"id": self.id, "regex": self.regex, "description": self.description,
                "scope": self.scope, "created_run": self.created_run, "stats": dict(self.stats),
                "reach": self.reach, "kind": self.kind}

    @property
    def holds(self) -> bool:
        """Is this reminder's moment BEFORE the action runs? (only then can it be held)"""
        return self.kind in HOLDING_KINDS


def read_kind(raw: object) -> str:
    """The kind a stored record means — `action` for anything unreadable.

    Lenient on purpose, and in the same way the rest of this loader is: a hand-edited file, a
    git sync from an older instance, or a record from before the kinds existed must read as the
    one kind it could have meant, never fail the run that loads it. The WRITE gate
    (`reminder_checks.kind_problem`) is where an unknown kind is refused.
    """
    return raw if isinstance(raw, str) and raw in KINDS else DEFAULT_KIND


def new_id(run_ts: str, taken: set[str]) -> str:
    """`rem-<run-ts>-<n>` — stable, sortable, and unique against ids already in the store."""
    n = 1
    while f"rem-{run_ts}-{n}" in taken:
        n += 1
    return f"rem-{run_ts}-{n}"


# --- the local store ---------------------------------------------------------------------

def local_path(routine_dir: Path) -> Path:
    return Path(routine_dir) / "state" / LOCAL_FILE


def _count(value: Any) -> int:
    """One tally field as a count — 0 for whatever a hand edit left that is not one."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _stats(raw: object) -> dict:
    got = raw if isinstance(raw, dict) else {}
    return {f: _count(got.get(f)) for f in STAT_FIELDS}


def load_local(routine_dir: Path) -> tuple[list[Reminder], dict[str, dict]]:
    """This routine's own reminders, plus its tally about GLOBAL ones. Lenient: a hand-broken
    file reads as an empty store rather than failing a run at boot — down to a single field,
    because this is read while the run is being constructed (`engine/remind.load`), where a
    `"fires": "many"` used to raise and the run never started.
    """
    raw = read_json(local_path(routine_dir), {})
    if not isinstance(raw, dict):
        return [], {}
    records = raw.get("reminders")
    out = []
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict) or not rec.get("id") or not rec.get("regex"):
            continue
        out.append(Reminder(id=str(rec["id"]), regex=str(rec["regex"]),
                            description=str(rec.get("description") or ""), scope="local",
                            created_run=str(rec.get("created_run") or ""),
                            stats=_stats(rec.get("stats")), kind=read_kind(rec.get("kind"))))
    tallies = raw.get("global_stats")
    gstats = {str(k): _stats(v) for k, v in (tallies if isinstance(tallies, dict) else {}).items()
              if isinstance(v, dict)}
    return out, gstats


def save_local(routine_dir: Path, reminders: list[Reminder],
               global_stats: dict[str, dict]) -> None:
    atomic_write_json(local_path(routine_dir), {
        "reminders": [{k: v for k, v in r.as_record().items() if k != "scope"}
                      for r in reminders if r.scope == "local"],
        "global_stats": {k: _stats(v) for k, v in sorted(global_stats.items())}})


# --- the global (library) store -----------------------------------------------------------

#: The library repo's subdir for the shared store, beside rules/ and permissions/.
REMINDERS_SUBDIR = "reminders"


def reminders_home(libraries_home: Path) -> Path:
    """The global store's directory inside a library repo. `ServerConfig.reminders_home` is
    this function applied to the configured libraries home — anything that has the repo root
    but no ServerConfig (the linter, a seed check) asks here.
    """
    return Path(libraries_home) / REMINDERS_SUBDIR


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
        if not isinstance(rec, dict) or not rec.get("regex") or rec.get("reach") not in REACHES:
            continue        # a record that cannot say whom it reaches reaches nobody
        rid = str(rec.get("id") or path.stem)
        if not is_reminder_id(rid):
            continue        # a record whose id could not be written back is not usable
        out.append(Reminder(id=rid, regex=str(rec["regex"]),
                            description=str(rec.get("description") or ""), scope="global",
                            created_run=str(rec.get("created_run") or ""),
                            stats=_stats(tally.get(rid)), reach=str(rec["reach"]),
                            kind=read_kind(rec.get("kind"))))
    return out


def records(reminders_home: Path) -> list[dict]:
    """EVERY file in the curated store as it stands — a record the engine would skip (no reach,
    an unusable id, unreadable JSON) included, because the Library tab is the one surface that
    can take a bad one out; a record it cannot see it cannot remove.
    """
    home = Path(reminders_home)
    out = []
    for path in sorted(home.glob("*.json")) if home.is_dir() else []:
        rec = read_json(path, {})
        rec = rec if isinstance(rec, dict) else {}
        out.append({"id": path.stem, "regex": str(rec.get("regex") or ""),
                    "description": str(rec.get("description") or ""),
                    "reach": str(rec.get("reach") or ""),
                    "kind": read_kind(rec.get("kind")),
                    "created_run": str(rec.get("created_run") or "")})
    return out


def write_global(reminders_home: Path, reminder: Reminder) -> Path:
    """Install/replace one curated reminder. The library's copy carries NO stats — the tally
    is each holder's own (see the module docstring).
    """
    path = global_path(reminders_home, reminder.id)
    rec = reminder.as_record()
    atomic_write_json(path, {k: rec[k] for k in ("id", "regex", "description", "reach",
                                                  "kind", "created_run")})
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

def active(routine_dir: Path, reminders_home: Path, level: str,
           listed: list[str] | tuple[str, ...] = ()) -> list[Reminder]:
    """The live set for one run: this routine's own reminders plus every curated one that
    reaches it (universal, or named in `listed`), with LOCAL OVERRIDING GLOBAL. Empty when the
    layer is off.

    Dedupe is by (regex, KIND) — the "same consequence class" test available to a machine. The
    kind belongs in the key because the same pattern on two different triggers is two different
    consequences: `^util:fs-ops mv ` as an `action` is "you are about to move a file", as a
    `result` it is "a move just reported something". Deduping those together would silence one
    of them for no reason a holder could see.
    """
    if LEVEL_RANK.get(level, 0) < LEVEL_RANK["local"]:
        return []
    local, gstats = load_local(routine_dir)
    seen = {(r.regex, r.kind) for r in local}
    return local + [g for g in load_global(reminders_home, gstats)
                    if (g.regex, g.kind) not in seen
                    and (g.reach == "universal" or g.id in listed)]


def matching(reminders: list[Reminder], target: str,
             kind: str = DEFAULT_KIND) -> list[Reminder]:
    """Every reminder of THAT kind whose pattern fires on that target.

    The kind is the caller's statement of which moment this is, so a `result` hook can never be
    tested against an action string and vice versa — the subjects are different and a pattern
    aimed at one says nothing about the other.
    """
    return [r for r in reminders if r.kind == kind and r.matches(target)]


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
