"""D152-C step 2 — a hook may be set for ANOTHER routine, and the target decides its fate.

Step 1 made the TRIGGER a named kind. The second genuine novelty of D152 option C is the
AUTHORITY: routine A can leave a caution on routine B. That is the same shape a `report` has —
A writes into B's own store, B reads it on its next run — with one addition a report does not
need: B meets the hook on every fire, so B must be able to dispose of it (keep it, remove it,
mute it for this run) and A must not be able to override that disposition.

Red-first for step 2. The invariants under test:

- a hook carries `owner` (the routine that SET it) and lives in the TARGET's local store;
- `owner` defaults to the holding routine, so every pre-step-2 record is its own owner and
  nothing changes for it;
- the target's disposition is recorded in the target's own store and is what the next run reads;
- the owner cannot rewrite a disposition the target made.
"""

import json

import pytest

from rsched import reminders as store
from rsched.reminders import Reminder


def _rem(rid="rem-1", regex="^util:danger", desc="it deletes the target", kind="action",
         scope="local", owner="", reach="", **stats):
    return Reminder(id=rid, regex=regex, description=desc, scope=scope, kind=kind, owner=owner,
                    created_run="r:1", stats={**store.blank_stats(), **stats},
                    reach=reach or ("universal" if scope == "global" else ""))


# --- the owner is part of a hook's identity ------------------------------------------------

def test_a_record_without_an_owner_is_its_holders_own(tmp_path):
    """Every reminder written before step 2 is one the routine left for itself."""
    path = store.local_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reminders": [
        {"id": "rem-old", "regex": "^util:danger", "description": "d",
         "created_run": "r:0", "stats": store.blank_stats()}]}), encoding="utf-8")
    loaded, _ = store.load_local(tmp_path)
    assert loaded[0].owner == ""          # "" means: mine, nobody else set it
    assert loaded[0].foreign is False


def test_an_owner_round_trips_and_marks_the_hook_foreign(tmp_path):
    store.save_local(tmp_path, [_rem(rid="rem-f", owner="watcher")], {})
    loaded, _ = store.load_local(tmp_path)
    assert (loaded[0].owner, loaded[0].foreign) == ("watcher", True)


# --- setting one for another routine -------------------------------------------------------

def test_set_for_writes_into_the_targets_own_store_and_stamps_the_owner(tmp_path):
    """The delivery shape a report already uses: the sender writes into the target's file."""
    target = tmp_path / "routines" / "builder"
    (target / "state").mkdir(parents=True)
    added = store.set_for(target, regex="^shell: git push", description="it publishes at once",
                          kind="action", owner="watcher", run_ts="20261001-120000")
    loaded, _ = store.load_local(target)
    assert [r.id for r in loaded] == [added.id]
    assert (loaded[0].owner, loaded[0].foreign) == ("watcher", True)
    assert loaded[0].regex == "^shell: git push"


def test_set_for_refuses_to_exceed_the_targets_cap(tmp_path):
    """The cap is a runaway backstop on the TARGET's turns, so a foreign writer obeys it too."""
    target = tmp_path / "routines" / "builder"
    (target / "state").mkdir(parents=True)
    store.save_local(target, [_rem(rid=f"rem-{n}", regex=f"^util:x{n}")
                              for n in range(store.MAX_LOCAL)], {})
    with pytest.raises(store.ReminderCapError):
        store.set_for(target, regex="^util:one-too-many", description="d", kind="action",
                      owner="watcher", run_ts="20261001-120000")


def test_set_for_will_not_duplicate_a_live_pattern_of_the_same_kind(tmp_path):
    target = tmp_path / "routines" / "builder"
    (target / "state").mkdir(parents=True)
    store.set_for(target, regex="^shell: git push", description="d", kind="action",
                  owner="watcher", run_ts="20261001-120000")
    with pytest.raises(store.ReminderDuplicateError):
        store.set_for(target, regex="^shell: git push", description="again", kind="action",
                      owner="watcher", run_ts="20261001-120001")
    # the same pattern on a DIFFERENT kind is a different consequence class and is allowed
    second = store.set_for(target, regex="^shell: git push", description="the result of it",
                           kind="result", owner="watcher", run_ts="20261001-120001")
    assert second.kind == "result"


# --- the target's disposition wins ---------------------------------------------------------

@pytest.mark.parametrize("disposition", list(store.DISPOSITIONS))
def test_every_disposition_round_trips_in_the_targets_own_store(tmp_path, disposition):
    store.save_local(tmp_path, [_rem(rid="rem-f", owner="watcher")], {})
    store.dispose(tmp_path, "rem-f", disposition)
    loaded, _ = store.load_local(tmp_path)
    if disposition == "remove":
        assert loaded == []
        return
    assert loaded[0].disposition == disposition


def test_a_muted_hook_stays_in_the_store_but_leaves_the_live_set(tmp_path):
    """`mute` is the target's "not this run" — the definition survives for its owner to see."""
    store.save_local(tmp_path, [_rem(rid="rem-f", owner="watcher")], {})
    store.dispose(tmp_path, "rem-f", "mute")
    home = tmp_path / "lib" / "reminders"
    assert [r.id for r in store.active(tmp_path, home, "local")] == []
    loaded, _ = store.load_local(tmp_path)
    assert loaded[0].disposition == "mute"


def test_the_owner_cannot_overwrite_a_disposition_the_target_made(tmp_path):
    """A foreign hook the target REMOVED must not come back because its owner set it again."""
    target = tmp_path / "routines" / "builder"
    (target / "state").mkdir(parents=True)
    added = store.set_for(target, regex="^shell: git push", description="d", kind="action",
                          owner="watcher", run_ts="20261001-120000")
    store.dispose(target, added.id, "remove")
    with pytest.raises(store.ReminderRefusedError):
        store.set_for(target, regex="^shell: git push", description="d", kind="action",
                      owner="watcher", run_ts="20261001-160000")
    # …and the refusal is about THIS pattern only: the owner may still set a different one
    other = store.set_for(target, regex="^shell: git tag", description="d", kind="action",
                          owner="watcher", run_ts="20261001-160000")
    assert other.regex == "^shell: git tag"


def test_a_removal_by_the_target_is_remembered_so_the_refusal_survives_the_run(tmp_path):
    target = tmp_path / "routines" / "builder"
    (target / "state").mkdir(parents=True)
    added = store.set_for(target, regex="^shell: git push", description="d", kind="action",
                          owner="watcher", run_ts="20261001-120000")
    store.dispose(target, added.id, "remove")
    raw = json.loads(store.local_path(target).read_text(encoding="utf-8"))
    assert raw["refused"] == [{"owner": "watcher", "regex": "^shell: git push",
                              "kind": "action"}]
