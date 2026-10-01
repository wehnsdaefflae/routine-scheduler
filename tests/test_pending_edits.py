"""D78-A pending-edit spool: queue validation + replay robustness."""

import pytest

from rsched import pending_edits


def test_queue_rejects_unknown_kind(tmp_path):
    """Fail closed: a kind with no applier can never enter the spool — nor can a trigger edit,
    whose routes are gone and whose appliers only drain spools written before 0.369.0."""
    for kind in ("bogus_kind", "trigger_create"):
        with pytest.raises(ValueError):
            pending_edits.queue(tmp_path, "r", kind, {})
    assert pending_edits.pending_count(tmp_path, "r") == 0


def _spooled_before_0369(home, kind: str, payload: dict) -> None:
    """A trigger edit as the deleted routes spooled it — the only way one exists now."""
    from rsched import spool

    spool.write(home, "pending-edits", "r", {"kind": kind, "payload": payload, "ts": "t"},
                prefix="pe")


def test_replay_records_failure_and_drops_file(tmp_path):
    """A single bad edit is surfaced (ok=False), its file dropped, and the rest still
    apply — one malformed edit must never wedge the whole queue."""
    home = tmp_path
    rdir = tmp_path / "routines" / "r"
    (rdir / "stages").mkdir(parents=True)
    (rdir / "routine.yaml").write_text("enabled: true\n", encoding="utf-8")

    # a file edit missing its required `path` key (KeyError in the applier) …
    pending_edits.queue(home, "r", "file", {"content": "no path"})
    # … followed by a valid file edit.
    pending_edits.queue(home, "r", "file", {"path": "stages/ok.md", "content": "good"})
    assert pending_edits.pending_count(home, "r") == 2

    rows = pending_edits.apply_pending(rdir, home, "r")
    assert len(rows) == 2
    assert rows[0]["ok"] is False and "KeyError" in rows[0]["error"]
    assert rows[1]["ok"] is True
    # both files consumed regardless of the failure; the good edit landed
    assert pending_edits.pending_count(home, "r") == 0
    assert (rdir / "stages" / "ok.md").read_text() == "good"


def _routine(tmp_path, yaml_text: str = "enabled: true\n"):
    rdir = tmp_path / "routines" / "r"
    (rdir / "stages").mkdir(parents=True)
    (rdir / "routine.yaml").write_text(yaml_text, encoding="utf-8")
    return rdir


def test_a_web_edit_commits_the_edited_file_and_nothing_else(tmp_path):
    """`edit <path> via web` must hold the edited file ALONE.

    The appliers staged the whole routine directory, so a web edit swept up whatever else
    happened to be dirty in that tree and shipped it under its own message. Seen live:
    library-sync commit 5baf069 "edit stages/export.md via web" carried that routine's pending
    run-retention changes (deleted and gzipped `runs/*`, a status.json) — the history says a
    stage module was edited and the diff says something else entirely. A routine's tree is
    dirty routinely: its own runs write to it while the web edit is queued.
    """
    from rsched import libgit

    rdir = _routine(tmp_path)
    libgit.init_repo(rdir)
    (rdir / "stages" / "export.md").write_text("first\n", encoding="utf-8")
    libgit.commit(rdir, "seed", routines_home=tmp_path)

    # what a run of this routine leaves lying around while the edit waits
    (rdir / "state").mkdir()
    (rdir / "state" / "status.json").write_text('{"runs": 3}\n', encoding="utf-8")
    (rdir / "stages" / "unrelated.md").write_text("not part of this edit\n", encoding="utf-8")

    pending_edits.apply_file(rdir, {"path": "stages/export.md", "content": "second\n"},
                             tmp_path, queued=True)

    named = libgit.git(rdir, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert named == ["stages/export.md"], f"the commit carried more than its file: {named}"
    # the bystanders are still uncommitted, exactly as they were
    assert libgit.git(rdir, "log", "--oneline", "--", "state/status.json").stdout.strip() == ""
    assert (rdir / "stages" / "export.md").read_text(encoding="utf-8") == "second\n"


def test_a_trigger_edit_commits_routine_yaml_and_nothing_else(tmp_path):
    """The same scoping for the trigger appliers, which share `_write_triggers`: they write one
    file (routine.yaml) and must commit that one.
    """
    from rsched import libgit

    rdir = _routine(tmp_path)
    libgit.init_repo(rdir)
    libgit.commit(rdir, "seed", routines_home=tmp_path)
    (rdir / "stages" / "bystander.md").write_text("dirty\n", encoding="utf-8")

    pending_edits.apply_trigger_create(
        rdir, {"entry": {"id": "t1", "type": "webhook", "token": "x"}}, tmp_path)

    named = libgit.git(rdir, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert named == ["routine.yaml"], f"the commit carried more than routine.yaml: {named}"


@pytest.mark.parametrize("broken", ["triggers: [never closed\n", "- a list\n- not a mapping\n"])
def test_a_routine_yaml_that_does_not_load_fails_one_edit_and_wedges_nothing(tmp_path, broken):
    """routine.yaml can be broken under a live run — by hand, or by the run's own `shell` or
    script, which the action-layer seal cannot stop (CLAUDE.md). The trigger appliers read it
    raw: the YAMLError (or the AttributeError of a non-mapping) escaped `apply_pending` AND
    the reap, with the edit still spooled — so the same edit failed at every later run's end
    and every edit queued behind it, of any kind, never applied."""
    rdir = _routine(tmp_path, broken)
    _spooled_before_0369(tmp_path, "trigger_create",
                         {"entry": {"id": "t1", "type": "webhook", "token": "x"}})
    pending_edits.queue(tmp_path, "r", "file", {"path": "stages/next.md", "content": "next"})

    rows = pending_edits.apply_pending(rdir, tmp_path, "r")

    assert [r["ok"] for r in rows] == [False, True]
    assert "routine.yaml" in rows[0]["error"]
    assert pending_edits.pending_count(tmp_path, "r") == 0, "the bad edit is dropped, not respooled"
    assert (rdir / "stages" / "next.md").read_text(encoding="utf-8") == "next"
    assert (rdir / "routine.yaml").read_text(encoding="utf-8") == broken, "never rewritten"


def test_a_replayed_file_edit_lands_atomically(tmp_path):
    """The endpoint writes an idle routine's file through `atomic_write`; the replay of the
    same edit wrote it in place, so a scan or a starting run could read half a recipe. An
    atomic write replaces the file (a new inode); an in-place write reuses the old one."""
    rdir = _routine(tmp_path)
    recipe = rdir / "main.md"
    recipe.write_text("old recipe\n", encoding="utf-8")
    before = recipe.stat().st_ino
    pending_edits.queue(tmp_path, "r", "file", {"path": "main.md", "content": "new recipe\n"})

    rows = pending_edits.apply_pending(rdir, tmp_path, "r")

    assert rows[0]["ok"] is True
    assert recipe.read_text(encoding="utf-8") == "new recipe\n"
    assert recipe.stat().st_ino != before, "written in place, not swapped in whole"


def test_the_trigger_appliers_round_trip(tmp_path):
    """create → retune → delete through the shared read/write pair, and a second report
    trigger slipping in while queued is skipped rather than duplicated."""
    rdir = _routine(tmp_path, "enabled: true\ntriggers:\n- {id: rep1, type: report}\n")
    _spooled_before_0369(tmp_path, "trigger_create", {"entry": {"id": "rep2", "type": "report"}})
    _spooled_before_0369(tmp_path, "trigger_create", {"entry": {"id": "hk", "type": "webhook"}})
    _spooled_before_0369(tmp_path, "trigger_update",
                         {"trigger_id": "hk", "fields": {"cooldown_s": 60}})
    _spooled_before_0369(tmp_path, "trigger_delete", {"trigger_id": "rep1"})
    _spooled_before_0369(tmp_path, "trigger_delete", {"trigger_id": "gone"})

    rows = pending_edits.apply_pending(rdir, tmp_path, "r")

    assert all(r["ok"] for r in rows)
    assert rows[0]["result"]["skipped"] and rows[4]["result"]["skipped"]
    import yaml
    saved = yaml.safe_load((rdir / "routine.yaml").read_text(encoding="utf-8"))
    assert saved == {"enabled": True, "triggers": [{"id": "hk", "type": "webhook",
                                                    "cooldown_s": 60}]}


def test_replay_empty_spool_is_noop(tmp_path):
    assert pending_edits.apply_pending(tmp_path / "r", tmp_path, "r") == []


def test_queue_order_survives_a_same_second_burst(tmp_path):
    """F298: filenames must sort in QUEUE order. The old name was second-resolution time
    plus RANDOM hex, so edits queued within one second replayed shuffled — this burst
    (queued far faster than a second) flushed that out reliably."""
    queued = [pending_edits.queue(tmp_path, "r", "file",
                                  {"path": f"stages/f{i}.md", "content": str(i)})
              for i in range(8)]
    assert [p.name for p in pending_edits.pending(tmp_path, "r")] == [p.name for p in queued]
