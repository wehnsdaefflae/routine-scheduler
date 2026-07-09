"""Workflow library lint + materialization + scaffold, against the real library-seed."""

from pathlib import Path

import pytest
import yaml

from rsched.config import ServerConfig, load_routine
from rsched.workflows.adapt import materialize
from rsched.workflows.lint import (lint_all, lint_materialized_text, lint_workflow_text)
from rsched.workflows.scaffold import scaffold

SEED = Path(__file__).resolve().parents[1] / "library-seed"


def test_seed_library_is_clean():
    results = lint_all(SEED)
    assert results, "seed library found"
    problems = {k: v for k, v in results.items() if v}
    assert problems == {}, problems


def test_lint_catches_defects():
    frags = ["ask-policy"]
    bad = """---
name: X
slug: mismatch
description: d
when_to_use: w
version: 1
status: wild
includes: [nope]
params: []
---
## Run flow
1. do {{undeclared}} things
"""
    problems = lint_workflow_text(bad, filename="bad.md", fragment_slugs=frags)
    text = " | ".join(problems)
    for needle in ("filename does not match", "status must be", "does not resolve",
                   "## Phases", "## Completion criteria", "undeclared"):
        assert needle in text, needle
    assert lint_workflow_text("no frontmatter at all", filename="x.md", fragment_slugs=[])


def test_materialize_inlines_fragments_and_provenance():
    content, prov = materialize(SEED, "general-task", self_flags={"fresh_eyes": False})
    assert prov["slug"] == "general-task" and prov["version"] == 5
    assert "## Standard practices" in content
    assert "### fragment: self-audit" in content
    assert "### fragment: ask-policy" in content
    assert "### fragment: web-research" in content         # always-on (no self-flag)
    assert "fragment: fresh-eyes" not in content          # toggled off
    assert "materialized_from" in content
    assert "tags:" not in content                          # fragment frontmatter must not leak into the prompt
    assert lint_materialized_text(content) == []


def test_tags_on_library_elements():
    from rsched import fragments_lib, utils_lib
    from rsched.workflows.library import list_workflows

    wfs = {w["slug"]: w for w in list_workflows(SEED)}
    assert "meta" in wfs["self-audit-code"]["tags"] and "meta" in wfs["meta-workflows"]["tags"]
    assert wfs["general-task"]["tags"] == ["general"]      # not meta → stays user-facing

    frags = {f["slug"]: f for f in fragments_lib.list_fragments(SEED / "fragments")}
    assert frags["web-research"]["tags"] == ["tool-use", "research"]
    assert frags["ask-policy"]["tags"] == ["policy"]
    # a fragment's frontmatter is stripped before its body is inlined into a prompt
    raw = (SEED / "fragments" / "web-research.md").read_text()
    assert raw.startswith("---") and fragments_lib.fragment_body(raw).lstrip().startswith("# fragment:")

    utils = {u["name"]: u for u in utils_lib.list_utils(SEED.parent / "util-seed")}
    assert utils["pytest-run"]["tags"] == ["dev", "testing"]
    assert utils["websearch"]["tags"] == ["web", "research"]


def test_suggest_candidate_filter_uses_meta_tag():
    from rsched.workflows.library import list_workflows
    from rsched.workflows.suggest import INTERNAL_TAG

    candidates = [w["slug"] for w in list_workflows(SEED)
                  if INTERNAL_TAG not in (w.get("tags") or []) and w["status"] == "stable"]
    assert "general-task" in candidates
    assert "meta-workflows" not in candidates and "self-audit-code" not in candidates


def test_lint_rejects_non_list_tags():
    from rsched.workflows.lint import lint_fragment_text

    bad_wf = ("---\nname: X\nslug: x\ndescription: d\nwhen_to_use: w\nversion: 1\n"
              "status: draft\ntags: not-a-list\n---\n## Run flow\n## Phases\n## Completion criteria\n")
    assert any("tags must be a list" in p for p in lint_workflow_text(bad_wf, filename="x.md", fragment_slugs=[]))
    bad_frag = "---\ntags: nope\n---\n# fragment: x — y\n\nbody line one\nbody line two\n"
    assert any("tags must be a list" in p for p in lint_fragment_text(bad_frag, filename="x.md"))


def test_scaffold_writes_and_loads_tags(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    d = scaffold(server, slug="tagged", name="Tagged", instruction="x",
                 workflow_slug="general-task", tags=["meta", "custom"])
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.tags == ["meta", "custom"]
    assert yaml.safe_load((d / "routine.yaml").read_text())["tags"] == ["meta", "custom"]


def test_materialize_missing_param():
    # inject a param requirement via a temp copy
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        shutil.copytree(SEED / "workflows", home / "workflows")
        shutil.copytree(SEED / "fragments", home / "fragments")
        wf = home / "workflows" / "general-task.md"
        text = wf.read_text().replace("params: []", "params: [deliverable]")
        wf.write_text(text.replace("## Run flow", "## Run flow\nDeliver {{deliverable}}."))
        with pytest.raises(KeyError):
            materialize(home, "general-task")
        content, _ = materialize(home, "general-task", params={"deliverable": "a weekly report"})
        assert "a weekly report" in content and "{{deliverable}}" not in content


def test_scaffold_creates_valid_routine(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    d = scaffold(server, slug="papers-radar", name="Papers radar",
                 instruction="# Instruction\n\nCollect papers.",
                 workflow_slug="general-task", cron="0 8 * * 1")
    cfg, problems = load_routine(d)
    assert cfg is not None and problems == [], problems
    assert cfg.cron == "0 8 * * 1" and cfg.workflow_slug == "general-task"
    assert (d / ".git").is_dir()
    assert (d / ".git" / "hooks" / "post-commit").stat().st_mode & 0o111
    # the workflow is REFERENCED, not materialized into the routine
    assert not (d / "workflow.md").exists()
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["budgets"]["max_turns"] == 60 and "self" not in raw
    # active fragments = the workflow's includes, materialized as editable routine files
    assert set(cfg.fragments) == set(raw["fragments"])
    assert "self-audit" in cfg.fragments and "global-utils" in cfg.fragments
    assert (d / "fragments" / "self-audit.md").exists()
    assert (d / ".gitignore").read_text().startswith("runs/")
    with pytest.raises(ValueError):
        scaffold(server, slug="papers-radar", name="dup", instruction="x",
                 workflow_slug="general-task")
    with pytest.raises(ValueError):
        scaffold(server, slug="Bad Slug", name="x", instruction="x",
                 workflow_slug="general-task")


def test_scaffold_writes_playbook_step_files(tmp_path):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir()
    server.library_home = SEED
    server.fragments_home = SEED / "fragments"
    d = scaffold(server, slug="split-routine", name="Split",
                 instruction="# Entry\n\nSteps in playbook/.", workflow_slug="general-task",
                 playbook={"discover": "# Discover step\n\nHow to discover.",
                           "compose.md": "# Compose step\n\nHow to compose."})
    assert (d / "playbook" / "discover.md").read_text().startswith("# Discover step")
    assert (d / "playbook" / "compose.md").read_text().startswith("# Compose step")
