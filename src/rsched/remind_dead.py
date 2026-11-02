"""Is a reminder row DEAD? — retiring a row in another routine's store without losing its tally.

D231, the operator's answer: "Give rules-review a way to delete/annotate a reminder row in a
DISABLED routine's store (an engine capability or a census verb), since no report can reach a
routine that never runs."

THE WALL IT REMOVES. A reminder store belongs to its routine, so the curator (`rules-review`)
does not write another routine's rows: its only channel is an addressed report, read on the
owner's NEXT SCHEDULED RUN. For a routine whose config says `enabled: false` that run never
comes, and four consecutive curation passes (2026-10-29 … 2026-11-01) recorded the same four
undeletable rows. `enabled` is deliberately consulted NOWHERE here: whether the holder ever runs
again is the holder's business, and a capability that only worked on disabled routines would
leave the curator unable to retire the row in the live routine next door.

WHY ANNOTATE RATHER THAN DELETE, in the operator's words: "a deletion loses the tally that
proves the pattern was too broad". One of those four rows carries 22 holds of which 8 are
labelled `could_not` — the evidence that the pattern fired where its consequence could not apply
IS the tally, so deleting the row destroys exactly what the curation pass exists to prove. The
primary act therefore marks the row dead and touches nothing else; `delete` stays as the
fallback for a row with no evidence worth keeping (one of the four has 0 holds and 0 matches).

THE MARKER IS A SIDECAR, not a field on the record: `{"dead": {"<rid>": {by, at, reason}}}`
beside `reminders`, `refused` and `global_stats` in the holder's own store file. Three reasons,
in order of weight:

1. the row is never rewritten, so this change cannot lose a tally even in principle — the
   evidence and the annotation have different writers (`reminders.record` vs. this module);
2. `reminders.py` is over the house size bar with ZERO slack (`tests/size_ratchet.json`), so a
   field on `Reminder` would have cost that file lines it may not gain; a sidecar costs it only
   LINE-NEUTRAL edits (the import, `save_local`'s carry, `active`'s filter);
3. a reader that does not know the key reads the store exactly as it did before, which is what
   keeps an annotation from being able to break the run of a routine that is re-enabled later.

The three properties the operator's answer demands, and where each is kept:

- **the tally survives** — `annotate` writes back every other key as it read it, and the
  holder's own later definition writes CARRY the annotation instead of dropping it
  (`engine/remind_write.save_local`, the same treatment a `mute` already gets there);
- **the row stops firing** — `live` takes it out of the live set at the one place the union is
  formed (`reminders.active`), so no fire point can see it: nothing matches it, nothing holds on
  it, nothing tallies against it, in the holder's runs or anywhere else;
- **the annotation says who, when and why** — `by` (the curating routine's slug), `at` (the
  moment) and `reason` (its prose), which is what makes a dead row answerable later without the
  run that wrote it.

AUTHORIZATION is not this module's business and gets no dial of its own: `capabilities.reminders:
global` — the curator's setting, which is what lets a routine write the SHARED store — is the
grant, and the engine seam (`engine/remind_write.curate`) asks `GrantPolicy.reminder_denial`
exactly once. A second gate over a grant pair the operator chose is not ours to build.
"""

from __future__ import annotations

from pathlib import Path

from .paths import atomic_write_json, read_json

#: The store file's key for the annotations, beside `reminders` / `refused` / `global_stats`.
DEAD_KEY = "dead"
#: What one annotation carries. `reason` is the whole point of annotating rather than deleting,
#: so a marker without one is not written and is not read (see `read_dead` and `annotate`).
FIELDS = ("by", "at", "reason")


def _store_path(routine_dir: Path) -> Path:
    """The holder's own store file. Asked of `reminders` rather than spelled again here: two
    spellings of one path is how two writers come to disagree about which file is the store.

    The import is deferred because `reminders` imports THIS module at its top (the filter and
    the carry are called from there) — this direction is the one that may not be eager.
    """
    from . import reminders

    return reminders.local_path(routine_dir)


def read_dead(routine_dir: Path) -> dict[str, dict]:
    """Every annotation in that routine's store, by reminder id.

    Lenient in the same way and for the same reason as every other reader of this file: a
    missing key, a hand edit or a half-written marker reads as "nothing is dead" rather than
    raising. This is read while a run is being constructed (`reminders.active`), where a store
    that could raise is a store that can stop a routine from starting.

    A marker with no `reason` is dropped: the reason is the annotation's substance, and a row
    retired with no stated reason is the one outcome worse than leaving it live.
    """
    raw = read_json(_store_path(routine_dir), {})
    marks = raw.get(DEAD_KEY) if isinstance(raw, dict) else None
    out: dict[str, dict] = {}
    for rid, mark in (marks if isinstance(marks, dict) else {}).items():
        if isinstance(mark, dict) and str(mark.get("reason") or "").strip():
            out[str(rid)] = {f: str(mark.get(f) or "") for f in FIELDS}
    return out


def live(routine_dir: Path, stored: list) -> list:
    """The records that still FIRE — every one no annotation has retired.

    Applied where the UNION is formed (`reminders.active`), so one filter serves every fire
    point there is: the pre-action hold, the observation kinds and the turn moments all read
    `loop.reminders`, and a dead row is simply not in it. Filtering at each fire point instead
    would be four places to forget, which is how `mute` became permanent (F697).
    """
    dead = read_dead(routine_dir)
    return [r for r in stored if r.id not in dead]


def forget(routine_dir: Path, rid: str) -> None:
    """Drop one row's annotation — called when the row itself is DELETED.

    Without it a `delete` of an already-dead row would leave an orphan marker keyed to an id
    no row has. Harmless to every reader (both the filter and the console decoration key by id
    against rows that exist), but a store that accumulates markers for rows it no longer holds
    is a store that lies about its own history, and the cleanup is four lines.
    """
    raw = read_json(_store_path(routine_dir), {})
    marks = raw.get(DEAD_KEY) if isinstance(raw, dict) else None
    if isinstance(raw, dict) and isinstance(marks, dict) and str(rid) in marks:
        atomic_write_json(_store_path(routine_dir),
                          {**raw, DEAD_KEY: {k: v for k, v in marks.items() if k != str(rid)}})


def carry(routine_dir: Path) -> dict:
    """The annotations as a fragment of the store file, for a rewrite to carry forward.

    `reminders.save_local` rewrites the whole file from the definitions it was handed, so a key
    it did not know about would be dropped on the next write — taking the annotation with it and
    resurrecting a row the curator had retired. Shaped as a fragment (`{}` or `{"dead": …}`) so
    the writer stays ONE expression on one line: that file may not gain a line.
    """
    dead = read_dead(routine_dir)
    return {DEAD_KEY: dead} if dead else {}


def annotate(routine_dir: Path, rid: str, *, by: str, reason: str, at: str) -> dict | None:
    """Mark ONE row dead in that routine's store; return the row's record with its marker, or
    None when the store holds no such id (or the reason is empty).

    A read-modify-write that touches nothing but the sidecar: the row, its `stats`, the refusal
    ledger and the global tallies are written back exactly as they were read. That is what "the
    tally survives" means operationally — there is no path through this function that could
    rewrite one.

    Annotating a row that is already dead REPLACES its marker: the later reason is the later
    fact about the row, the rule the hook stream applies to a revise for the same reason.
    """
    raw = read_json(_store_path(routine_dir), {})
    if not isinstance(raw, dict) or not str(reason or "").strip():
        return None
    rows = [r for r in (raw.get("reminders") or []) if isinstance(r, dict)]
    row = next((r for r in rows if str(r.get("id") or "") == str(rid)), None)
    if row is None:
        return None
    mark = {"by": str(by or ""), "at": str(at or ""), "reason": str(reason)}
    atomic_write_json(_store_path(routine_dir),
                      {**raw, DEAD_KEY: {**read_dead(routine_dir), str(rid): mark}})
    return {**row, DEAD_KEY: mark}


def decorate(routine_dir: Path, rows: list) -> list[dict]:
    """Those reminders as records, each carrying its annotation when it has one.

    The console's Recipe health section lists "every reminder in force" straight from the store
    (`readmodels/run_health.cautions`), which is the one surface a person reviews a pattern on.
    Without this, a retired row would read there as live and the curator's reason — and the fact
    that a row was retired at all — would exist nowhere a person looks.
    """
    dead = read_dead(routine_dir)
    return [{**r.as_record(), **({DEAD_KEY: dead[r.id]} if r.id in dead else {})}
            for r in rows]


def line(rid: str, mark: dict) -> str:
    """One line about a dead row — what the curating run reads back on its own turn."""
    return (f"{rid} marked DEAD by {mark.get('by') or 'a curator'} on "
            f"{mark.get('at') or 'an unrecorded date'}: {mark.get('reason') or ''}")
