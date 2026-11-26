"""The one pre-D174 spelling still read — a check's `task:` — and the three that are not."""

from __future__ import annotations

import json

from rsched import jobs, jobs_compat
from rsched.engine.actionschema import ACTION_SCHEMA, KINDS
from rsched.engine.completion import diagnose
from rsched.engine.renamedkinds import hint
from rsched.grants import effective_settings, normalize_capabilities
from rsched.schema_guard import validate

# ── the run-gate check key: the one alias still read ──────────────────────────────────────

def test_a_check_may_name_its_job_under_either_key():
    both = [{"id": "c1", "kind": "mail", "task": "alpha"},
            {"id": "c2", "kind": "mail", "job": "beta"}]
    assert jobs.checks_by_job(both) == {"alpha": ["c1"], "beta": ["c2"]}
    assert jobs_compat.check_job(both[0]) == "alpha"
    assert jobs_compat.check_job(both[1]) == "beta"
    assert jobs_compat.check_job({"id": "c3"}) == ""


def test_the_canonical_check_key_wins_when_a_check_carries_both():
    assert jobs_compat.check_job({"id": "c1", "job": "beta", "task": "alpha"}) == "beta"


def test_both_keys_are_accepted_vocabulary_so_a_live_config_does_not_read_as_broken():
    from rsched.gatekit import validate as validate_checks
    for key in ("job", "task"):
        problems = validate_checks([{"id": "c1", "kind": "mail", key: "alpha",
                                     "host": "h", "user_secret": "U", "password_secret": "P"}])
        assert problems == [], (key, problems)


# ── the three spellings that are NOT read any more ────────────────────────────────────────

def test_the_pre_rename_capabilities_key_is_not_read_and_reads_as_an_unknown_key():
    """The capability half of the deprecation is gone: every installed routine.yaml spells it
    `jobs:`, so a file still naming `tasks:` is told so rather than silently switching the
    layer on under a spelling nothing writes."""
    for raw in ({"tasks": "on"}, {"tasks": True}):
        caps, problems = normalize_capabilities(raw)
        assert caps == {}, raw
        assert any("capabilities.tasks: unknown key" in p for p in problems), raw
        assert effective_settings(caps)["jobs"] == "off", raw


def test_the_pre_rename_store_file_is_not_read(tmp_path):
    """The store half converged by itself — the first save after the rename wrote the canonical
    name — so the old file is the routine's own data and nothing reads it; it is never deleted
    either, and a store that is not read cannot report problems."""
    d = tmp_path / "fau"
    (d / "state").mkdir(parents=True)
    (d / "state" / "tasks.json").write_text(json.dumps(
        {"version": 1, "tasks": [{"id": "nanogeofeld", "title": "NanoGeofeld", "brief": "b",
                                  "state": "active", "workspace": "tasks/nanogeofeld"}],
         "deleted": [], "run": {}}), encoding="utf-8")
    assert jobs.store_path(d).name == "jobs.json"
    assert jobs.load(d)["jobs"] == []
    assert jobs.problems(d) == []
    assert (d / "state" / "tasks.json").is_file()


def test_the_pre_rename_list_key_inside_the_store_is_not_read(tmp_path):
    d = tmp_path / "fau"
    (d / "state").mkdir(parents=True)
    (d / "state" / "jobs.json").write_text(
        json.dumps({"version": 1, "tasks": [{"id": "nanogeofeld"}], "deleted": [], "run": {}}),
        encoding="utf-8")
    assert jobs.load(d)["jobs"] == []
    assert jobs.problems(d) == []


def test_a_default_workspace_for_a_new_job_is_the_new_name():
    assert jobs.workspace_rel({"id": "fresh"}) == "jobs/fresh"


# ── the action kind, which gets NO alias ──────────────────────────────────────────────────

def test_the_retired_kind_is_gone_from_the_schema():
    assert "task" not in KINDS and "job" in KINDS
    stale = {"say": "s", "kind": "task", "verb": "list"}
    assert validate(stale, ACTION_SCHEMA) != []


def test_the_retired_kind_gets_no_correction_because_nothing_spells_it():
    """D174's rename register pointed `task` at `job`. It was removed once a sweep of every
    recipe, library pattern and seed on disk found the kind spelled nowhere — the correction
    had never fired, and a shim nothing reaches is what the no-backwards-compatibility rule
    is about. The CONFIG key `task` keeps its own compatibility above; the ACTION KIND is
    simply refused, with no promise of a resend.
    """
    stale = {"say": "s", "kind": "task", "verb": "checkpoint", "id": "nanogeofeld",
             "outcome": "advanced", "summary": "x"}
    assert validate(stale, ACTION_SCHEMA) != []
    assert hint(stale) == ""
    assert "job" not in diagnose(stale)


def test_an_ordinary_invalid_action_gets_no_retired_kind_hint():
    assert hint({"kind": "util", "name": "codemap"}) == ""
    assert hint({"kind": "job", "verb": "list"}) == ""
    assert hint({}) == ""
