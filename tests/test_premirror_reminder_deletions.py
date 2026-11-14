"""D152 step 4: the pre-mirror DELETIONS the stream never heard about — goal b4.

WHERE THE CAMPAIGN STANDS, measured on the live instance with `hooks/reminderproj.drift` over
all 31 routines:

  2026-11-14, before 0.542.0   122 drifting ids in 21 routines — 95 store-only, 23 stream-only,
                                4 differs
  2026-11-14, after  0.542.0    27 drifting ids in  9 routines —  0 store-only, 23 stream-only,
                                4 differs

The `store-only` class — the one that mattered most, because a stream-served read would have
LOST 95 live patterns — is now EMPTY, which is 0.542.0's backfill working on the real instance.
What is left is 23 `stream-only` ids and 4 `differs`.

WHY THE 23 ARE NOT "CORRECT HISTORY TO LEAVE ALONE", which is what 0.542.0's own module said.
They are reminders a routine DELETED before `remind_write.remove` learned to mirror the deletion
(0.542.0). The stream therefore still implies a definition the store has dropped — and the
campaign's end state is the store SERVED from the stream, so a fold that cannot see those
deletions would **re-raise 23 patterns their own routines removed**. A reminder decides whether
an action is HELD, so that is a change to what binds a run, not a stale row.

AND THE FIX IS NOT A DELETION FROM THE STREAM, which an append-only stream must never suffer.
It is the row the write path would have written: one `disposition: remove` hook per id, which
`reminderproj.REMOVING` already subtracts. The history stays — the definition was written, then
removed, and both facts are in the stream, in that order.

WHAT IS DELIBERATELY LEFT: the 4 `differs`. Three are missing `created_run` (the mirror era
dropped it) and one disagrees on `owner` (`rem-20261026-233255-1`: `vodafone-outage-claims` on
the stream, `self-audit` in the store). `owner` decides who may REMOVE a hook, so a migration
may not settle it by guessing — it is a question for the operator, and this module names it in
its record instead of answering it.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.hooks import reminderproj
from rsched.hooks import store as hookstore
from rsched.migrate_premirror_deletions import missing_removals, run_migration


class _FakeServer:
    def __init__(self, home: Path):
        self.routines_home = home


def _routine(home: Path, slug: str, reminders: dict) -> Path:
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"name: {slug}\n", encoding="utf-8")
    (d / "state").mkdir(exist_ok=True)
    (d / "state" / "reminders.json").write_text(json.dumps(reminders), encoding="utf-8")
    return d


def _hook(home: Path, slug: str, rid: str, *, regex: str = "^shell: rm ",
          disposition: str = "keep", target: str = "") -> None:
    hookstore.record_reminder(
        home, record={"id": rid, "regex": regex, "description": "d", "kind": "action",
                      "scope": "local", "reach": "", "created_run": f"{slug}:20261001-000000",
                      "owner": slug, "disposition": disposition},
        routine=slug, run_id=f"{slug}:20261001-000000", target=target,
        disposition=disposition)


# ---- what the migration finds, and what it refuses to touch -------------------------------

def test_a_stream_only_id_is_named_as_a_missing_removal(tmp_path):
    d = _routine(tmp_path, "a-routine", {"reminders": []})
    _hook(tmp_path, "a-routine", "rem-20261001-000000-1")
    assert [r["id"] for r in missing_removals(tmp_path, d)] == ["rem-20261001-000000-1"]


def test_an_id_the_store_still_holds_is_not_a_deletion(tmp_path):
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", {"reminders": [
        {"id": rid, "regex": "^shell: rm ", "description": "d", "kind": "action",
         "scope": "local", "reach": "", "created_run": "a-routine:20261001-000000",
         "owner": "a-routine", "disposition": "keep"}]})
    _hook(tmp_path, "a-routine", rid)
    assert missing_removals(tmp_path, d) == []


def test_an_id_already_carrying_a_remove_row_is_left_alone(tmp_path):
    """Idempotence at the level that matters: the projection subtracts on `remove`, so such an
    id is not drifting at all and a second row would be noise in an append-only stream."""
    d = _routine(tmp_path, "a-routine", {"reminders": []})
    rid = "rem-20261001-000000-1"
    _hook(tmp_path, "a-routine", rid)
    _hook(tmp_path, "a-routine", rid, disposition="remove")
    assert missing_removals(tmp_path, d) == []


def test_a_content_disagreement_is_not_a_deletion(tmp_path):
    """The 4 live `differs` ids: an append cannot settle a disagreement about content, and the
    `owner` one decides who may remove a hook — a migration may not guess it."""
    rid = "rem-20261001-000000-1"
    d = _routine(tmp_path, "a-routine", {"reminders": [
        {"id": rid, "regex": "^shell: rm ", "description": "d", "kind": "action",
         "scope": "local", "reach": "", "created_run": "", "owner": "someone-else",
         "disposition": "keep"}]})
    _hook(tmp_path, "a-routine", rid)
    assert missing_removals(tmp_path, d) == []


# ---- what it writes -----------------------------------------------------------------------

def test_the_migration_empties_the_comparator_for_a_deleted_reminder(tmp_path):
    """The campaign's own property, end to end: after the migration the comparator reads EMPTY
    for that routine, because the projection subtracts the `remove` row."""
    d = _routine(tmp_path, "a-routine", {"reminders": []})
    _hook(tmp_path, "a-routine", "rem-20261001-000000-1")
    assert reminderproj.drift(tmp_path, d)
    record = run_migration(_FakeServer(tmp_path))
    assert record["removed_total"] == 1
    assert reminderproj.drift(tmp_path, d) == {}


def test_the_definition_is_not_deleted_from_the_stream(tmp_path):
    """An append-only stream must never lose a row: the definition stays and the removal is a
    LATER fact about the same id. Both are history, in that order."""
    _routine(tmp_path, "a-routine", {"reminders": []})
    rid = "rem-20261001-000000-1"
    _hook(tmp_path, "a-routine", rid)
    before = len(hookstore.read_hooks(tmp_path))
    run_migration(_FakeServer(tmp_path))
    after = hookstore.read_hooks(tmp_path)
    assert len(after) == before + 1, "a row was appended, not replaced"
    rows = [h for h in after if h.id == rid and h.source == "reminder"]
    assert [h.disposition for h in rows] == ["keep", "remove"], (
        "the definition stays and the removal is a LATER row about the same id")


def test_it_runs_once_and_records_what_it_did(tmp_path):
    d = _routine(tmp_path, "a-routine", {"reminders": []})
    _hook(tmp_path, "a-routine", "rem-20261001-000000-1")
    first = run_migration(_FakeServer(tmp_path))
    assert first["removed_total"] == 1
    assert first["removed"] == {"a-routine": ["rem-20261001-000000-1"]}
    assert (tmp_path / ".control" / "migrations" / "premirror-deletions.json").is_file()
    assert run_migration(_FakeServer(tmp_path)) == {}, "a one-shot migration runs once"
    assert reminderproj.drift(tmp_path, d) == {}


def test_the_record_names_what_it_deliberately_did_not_settle(tmp_path):
    """The `differs` ids stay in the record as a question, not as a silence: `owner` decides who
    may remove a hook, and the live disagreement (rem-20261026-233255-1) is the operator's."""
    rid = "rem-20261001-000000-1"
    _routine(tmp_path, "a-routine", {"reminders": [
        {"id": rid, "regex": "^shell: rm ", "description": "d", "kind": "action",
         "scope": "local", "reach": "", "created_run": "", "owner": "someone-else",
         "disposition": "keep"}]})
    _hook(tmp_path, "a-routine", rid)
    record = run_migration(_FakeServer(tmp_path))
    assert record["still_drifting"].get("a-routine") == 1
    assert "owner" in record["note"]


def test_a_routine_with_nothing_to_remove_is_not_touched(tmp_path):
    _routine(tmp_path, "a-routine", {"reminders": []})
    record = run_migration(_FakeServer(tmp_path))
    assert record["removed_total"] == 0
    assert record["removed"] == {}


def test_the_live_instance_has_only_the_differs_class_left_after_this(tmp_path):
    """The campaign's property, measured against the LIVE stores through the PURE finder — no
    write, no daemon boot, the shape `migrate_mirror_hooks` established.

    Measured 2026-11-14 after 0.542.0: 27 drifting ids in 9 routines, of which this migration
    accounts for 23 in 8 routines, leaving exactly the 4 `differs`. The assertion is the
    RELATION (what is left is only `differs`), not the number: a new drift of a different class
    must fail here, while a legitimately deleted reminder tomorrow must not.
    """
    import os

    from rsched.hooks import reminderproj as proj
    from rsched.migrate_premirror_deletions import missing_removals as live_missing
    from rsched.migrate_premirror_deletions import routine_dirs as live_dirs

    home = Path(os.environ.get("RSCHED_ROUTINES_HOME", "/home/mark/routines"))
    if not home.is_dir():
        return                                   # not this box: nothing to measure
    left = []
    for d in live_dirs(home):
        would = {r["id"] for r in live_missing(home, d)}
        for rid, (stream, stored) in proj.drift(home, d).items():
            if rid in would:
                continue
            left.append((d.name, rid, bool(stream), bool(stored)))
    for _slug, _rid, has_stream, has_store in left:
        assert has_stream and has_store, (
            f"an id left drifting is neither store-only nor a recorded deletion: {left}")


def test_the_migration_declares_its_expiry(tmp_path):
    """The repo's own policy check (`tests/test_policy.py`) requires it, and the reason is this
    module's whole nature: a one-shot is dead code the moment every instance has run it."""
    src = Path("src/rsched/migrate_premirror_deletions.py").read_text(encoding="utf-8")
    assert "MIGRATION(expires=" in src
