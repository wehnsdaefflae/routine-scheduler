"""What a routine already on disk keeps working with after D174 renamed the layer to JOBS.

The rename is the whole point of D174 (`task` named two unrelated things), but the routines
holding this layer were written before it and the operator's files are not this code's to
rewrite. `fau` on 2026-10-12 is the specimen: `capabilities: {tasks: 'on'}`, a 134 KB
`state/tasks.json` with five records, ~40 run-gate checks keyed `task: <id>`, and five
workspaces under `tasks/`. Every one of those spellings is READ as the canonical name and never
written back, so nothing has to be migrated for that routine's next run to work.

The one thing that does NOT get an alias is the ACTION KIND: D174 chose zero residual ambiguity,
so `kind: "task"` is gone and a run still emitting it is TOLD its new name
(engine/renamedkinds.py) instead of being refused blankly — which is the F637 failure, three
rejections of one correctable mistake.
"""

from __future__ import annotations

import json

from rsched import jobs, jobs_compat
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.completion import diagnose
from rsched.engine.renamedkinds import hint
from rsched.grants import effective_settings, normalize_capabilities
from rsched.schema_guard import validate

# ── the capabilities key ──────────────────────────────────────────────────────────────────

def test_the_pre_rename_capabilities_key_still_switches_the_layer_on():
    for raw in ({"tasks": "on"}, {"tasks": True}):
        caps, problems = normalize_capabilities(raw)
        assert problems == [], raw
        assert caps == {"jobs": "on"}, raw            # normalized to the canonical key
        assert effective_settings(caps)["jobs"] == "on", raw


def test_the_canonical_key_wins_when_a_file_carries_both():
    caps, problems = normalize_capabilities({"jobs": "off", "tasks": "on"})
    assert problems == [] and caps == {"jobs": "off"}


def test_a_bad_value_is_reported_under_the_canonical_name_whichever_key_gave_it():
    for raw in ({"tasks": "maybe"}, {"jobs": "maybe"}):
        caps, problems = normalize_capabilities(raw)
        assert caps == {} and problems == ["capabilities.jobs must be off or on"], raw


def test_a_permission_doc_still_cannot_require_the_layer_under_either_name():
    for key in ("jobs", "tasks"):
        _, problems = normalize_capabilities({key: "on"}, label="requires", requires=True)
        assert any("unknown key" in p for p in problems), key


# ── the store file ────────────────────────────────────────────────────────────────────────

def _legacy_store(routine_dir):
    routine_dir.mkdir(parents=True, exist_ok=True)
    (routine_dir / "state").mkdir(exist_ok=True)
    (routine_dir / "state" / "tasks.json").write_text(json.dumps(
        {"version": 1, "tasks": [{"id": "nanogeofeld", "title": "NanoGeofeld", "brief": "b",
                                  "state": "active", "workspace": "tasks/nanogeofeld"}],
         "deleted": [], "run": {}}), encoding="utf-8")


def test_the_pre_rename_store_is_read_while_it_is_the_only_one_on_disk(tmp_path):
    d = tmp_path / "fau"
    _legacy_store(d)
    doc = jobs.load(d)
    assert [j["id"] for j in doc["jobs"]] == ["nanogeofeld"]
    assert jobs.read_path(d).name == "tasks.json"
    # and a stored workspace is honoured exactly as written — no directory moves
    assert jobs.workspace_rel(doc["jobs"][0]) == "tasks/nanogeofeld"


def test_the_first_save_writes_the_canonical_file_and_leaves_the_old_one_alone(tmp_path):
    d = tmp_path / "fau"
    _legacy_store(d)
    jobs.save(d, jobs.load(d))
    assert (d / "state" / "jobs.json").is_file()
    assert (d / "state" / "tasks.json").is_file()      # never deleted: it is the operator's data
    assert jobs.read_path(d).name == "jobs.json"       # the canonical one wins once it exists
    assert [j["id"] for j in jobs.load(d)["jobs"]] == ["nanogeofeld"]


def test_store_problems_name_whichever_file_they_were_found_in(tmp_path):
    d = tmp_path / "fau"
    (d / "state").mkdir(parents=True)
    (d / "state" / "tasks.json").write_text('{"tasks": [{"id": "Not A Slug"}]}', encoding="utf-8")
    assert any("tasks.json" in p for p in jobs.problems(d))


def test_a_default_workspace_for_a_new_job_is_the_new_name():
    assert jobs.workspace_rel({"id": "fresh"}) == "jobs/fresh"


# ── the run-gate check key ────────────────────────────────────────────────────────────────

def test_a_check_may_name_its_job_under_either_key():
    both = [{"id": "c1", "kind": "mail", "task": "alpha"},
            {"id": "c2", "kind": "mail", "job": "beta"}]
    assert jobs.checks_by_job(both) == {"alpha": ["c1"], "beta": ["c2"]}
    assert jobs_compat.check_job(both[0]) == "alpha"
    assert jobs_compat.check_job({"id": "c3"}) == ""


def test_both_keys_are_accepted_vocabulary_so_a_live_config_does_not_read_as_broken():
    from rsched.gatekit import validate as validate_checks
    for key in ("job", "task"):
        problems = validate_checks([{"id": "c1", "kind": "mail", key: "alpha",
                                     "host": "h", "user_secret": "U", "password_secret": "P"}])
        assert problems == [], (key, problems)


# ── the action kind, which gets NO alias ──────────────────────────────────────────────────

def test_the_retired_kind_is_gone_from_the_schema():
    assert "task" not in KINDS and "job" in KINDS
    stale = {"say": "s", "kind": "task", "verb": "list"}
    assert validate(stale, ACTION_SCHEMA) != []


def test_a_run_emitting_the_retired_kind_is_told_its_new_name():
    stale = {"say": "s", "kind": "task", "verb": "checkpoint", "id": "nanogeofeld",
             "outcome": "advanced", "summary": "x"}
    said = hint(stale)
    assert "`job` now" in said and "kind='job'" in said
    assert "D174" in said and "recipe" in said
    # it reaches the model through the retry's diagnosis channel
    assert said in diagnose(stale)


def test_an_ordinary_invalid_action_gets_no_renamed_kind_hint():
    assert hint({"kind": "util", "name": "codemap"}) == ""
    assert hint({"kind": "job", "verb": "list"}) == ""
    assert hint({}) == ""
