"""D152 step 4, the last class: a definition DISAGREEMENT is settled by the store — goal b9.

THE OPERATOR, 2026-11-14, answering the question this run put to him:
*"The STORE is authoritative (self-audit owns it) — the holder's own record wins"*,
chosen over "the stream wins", "delete the reminder" and "leave it drifting as a permanent
exception".

WHAT HE WAS ANSWERING. `hooks/reminderproj.drift` compares the definition the hook stream
implies against the one the routine's store holds. Measured over all 31 live routines today:

  before 0.542.0   122 drifting ids in 21 routines — 95 store-only, 23 stream-only, 4 differs
  after  0.542.0    27 in  9 —  0 store-only, 23 stream-only, 4 differs
  after  0.548.0     4 in  2 —  0 store-only,  0 stream-only, 4 differs

So this is the last class, and three of its four ids are mechanical (a missing `created_run`,
which the mirror era dropped, as `hooks/store.record_reminder`'s own docstring admits). The
fourth is why it needed him: `rem-20261026-233255-1` reads `owner: vodafone-outage-claims` on
the stream and `owner: self-audit` in the store, and `owner` decides who may REMOVE a hook. A
migration guessing that would hand one routine power over another routine's reminder.

WHY HIS ANSWER IS ALSO THE RIGHT GENERAL RULE, which is what makes it safe to apply to all four:
the store is where the routine that HOLDS the reminder writes it, through `remind_write.persist`
— the one path a routine's own act goes through. The stream's row is a MIRROR of that act, and
every disagreement measured here is the mirror being poorer (a dropped field, an origin recorded
as the holder instead of the owner), never the store being poorer. Serving the store from the
stream is the campaign's end state, so the stream has to carry what the store says, exactly.

AND IT IS STILL AN APPEND, never an edit: the stream gains a later row carrying the store's own
record. The poorer row stays as history, and the fold — last row wins, by id — reads the
agreement.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.hooks import reminderproj
from rsched.hooks import store as hookstore
from rsched.migrate_store_wins import disagreements, run_migration


class _FakeServer:
    def __init__(self, home: Path):
        self.routines_home = home


def _routine(home: Path, slug: str, reminders: list[dict]) -> Path:
    d = home / slug
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"name: {slug}\n", encoding="utf-8")
    (d / "state" / "reminders.json").write_text(json.dumps({"reminders": reminders}),
                                                 encoding="utf-8")
    return d


def _record(rid: str, **over) -> dict:
    """A reminder as its store holds it. `owner` is EMPTY for a self-held one — measured across
    all 195 live reminders: 194 empty, and the single exception is the id the operator ruled on
    (`rem-20261026-233255-1`, a hook a sibling set). The projection derives `owner: ""` for a
    `self` addressee, so a fixture naming the routine's own slug would invent a disagreement no
    real store has."""
    return {"id": rid, "regex": "^shell: rm ", "description": "d", "kind": "action",
            "scope": "local", "reach": "", "created_run": "a-routine:20261001-000000",
            "owner": "", "disposition": "keep", **over}


# ---- what it finds -----------------------------------------------------------------------

def test_a_missing_created_run_is_a_disagreement_the_store_settles(tmp_path):
    """Three of the four live ids are exactly this: the mirror era wrote the hook without
    `created_run`, the only record of when a definition entered."""
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", [_record(rid)])
    hookstore.record_reminder(tmp_path, record=_record(rid, created_run=""),
                              routine="a-routine", run_id="")
    found = disagreements(tmp_path, d)
    assert [r["id"] for r in found] == [rid]
    assert found[0]["created_run"] == "a-routine:20261001-000000", "the STORE's value"


def test_an_owner_disagreement_is_settled_the_way_he_ruled(tmp_path):
    """The live specimen `rem-20261026-233255-1`, and the reason he was asked: `owner` decides
    who may REMOVE a hook."""
    rid = "rem-20261026-233255-1"
    d = _routine(tmp_path, "holder", [_record(rid, owner="self-audit")])
    hookstore.record_reminder(tmp_path, record=_record(rid, owner="someone-else"),
                              routine="someone-else", run_id="x", target="holder")
    found = disagreements(tmp_path, d)
    assert [r["id"] for r in found] == [rid]
    assert found[0]["owner"] == "self-audit", "the holder's own record wins (his words)"


def test_an_agreeing_id_is_not_touched(tmp_path):
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", [_record(rid)])
    hookstore.record_reminder(tmp_path, record=_record(rid), routine="a-routine",
                              run_id="a-routine:20261001-000000")
    assert disagreements(tmp_path, d) == []


def test_a_store_only_or_stream_only_id_is_not_this_migrations_business(tmp_path):
    """Those are 0.542.0's backfill and 0.548.0's deletions. One class per migration, so a
    record says which one acted."""
    d = _routine(tmp_path, "a-routine", [_record("rem-store-only-1")])
    hookstore.record_reminder(tmp_path, record=_record("rem-stream-only-1"),
                              routine="a-routine", run_id="x")
    assert disagreements(tmp_path, d) == []


# ---- what it writes ----------------------------------------------------------------------

def test_the_repair_empties_the_comparator(tmp_path):
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", [_record(rid)])
    hookstore.record_reminder(tmp_path, record=_record(rid, created_run=""),
                              routine="a-routine", run_id="")
    assert reminderproj.drift(tmp_path, d)
    record = run_migration(_FakeServer(tmp_path))
    assert record["repaired_total"] == 1
    assert reminderproj.drift(tmp_path, d) == {}


def test_the_poorer_row_stays_as_history(tmp_path):
    """An append-only stream never loses a row: the fold is last-row-wins by id, so the
    agreement is read off the new row and the old one remains evidence of the mirror era."""
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", [_record(rid)])
    hookstore.record_reminder(tmp_path, record=_record(rid, created_run=""),
                              routine="a-routine", run_id="")
    before = len(hookstore.read_hooks(tmp_path))
    run_migration(_FakeServer(tmp_path))
    rows = [h for h in hookstore.read_hooks(tmp_path) if h.id == rid]
    assert len(hookstore.read_hooks(tmp_path)) == before + 1
    assert len(rows) == 2, "the poorer row stays"
    assert reminderproj.drift(tmp_path, d) == {}


def test_a_foreign_hook_keeps_its_holder_and_gains_the_stores_owner(tmp_path):
    """Both halves at once: the row must stay addressed to the HOLDER (or the holder loses
    sight of a hook set on it) while its origin becomes the owner the store names."""
    rid = "rem-20261026-233255-1"
    d = _routine(tmp_path, "holder", [_record(rid, owner="self-audit")])
    hookstore.record_reminder(tmp_path, record=_record(rid, owner="someone-else"),
                              routine="someone-else", run_id="x", target="holder")
    run_migration(_FakeServer(tmp_path))
    assert reminderproj.drift(tmp_path, d) == {}
    held = reminderproj.definitions(tmp_path, routine="holder")
    assert held[rid]["owner"] == "self-audit"


def test_it_runs_once_and_records_what_it_settled(tmp_path):
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", [_record(rid)])
    hookstore.record_reminder(tmp_path, record=_record(rid, created_run=""),
                              routine="a-routine", run_id="")
    first = run_migration(_FakeServer(tmp_path))
    assert first["repaired"] == {"a-routine": [rid]}
    assert "store is authoritative" in first["note"].lower()
    assert run_migration(_FakeServer(tmp_path)) == {}
    assert reminderproj.drift(tmp_path, d) == {}


def test_the_migration_declares_its_expiry():
    src = Path("src/rsched/migrate_store_wins.py").read_text(encoding="utf-8")
    assert "MIGRATION(expires=" in src
    assert "holder's own record wins" in src, "his ruling is quoted where it is implemented"


# ---- the live property: the comparator reads EMPTY ---------------------------------------

def test_the_live_comparator_would_read_empty_after_this(tmp_path):
    """The campaign's hard precondition, measured through the PURE finder against the live
    stores — no write, no daemon boot. Nothing may be left drifting that this migration and the
    two before it do not account for; if something is, goal b9 is not met and this must fail.
    """
    import os

    from rsched.migrate_premirror_deletions import missing_removals
    from rsched.migrate_premirror_deletions import routine_dirs as live_dirs
    from rsched.migrate_store_wins import disagreements as live_differs

    home = Path(os.environ.get("RSCHED_ROUTINES_HOME", "/home/mark/routines"))
    if not home.is_dir():
        return
    left = []
    for d in live_dirs(home):
        accounted = ({r["id"] for r in missing_removals(home, d)}
                     | {r["id"] for r in live_differs(home, d)})
        left += [(d.name, rid) for rid in reminderproj.drift(home, d) if rid not in accounted]
    assert left == [], f"ids no migration accounts for: {left}"
