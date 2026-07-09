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
    assert prov["slug"] == "general-task" and prov["version"] == 3
    assert "## Standard practices" in content
    assert "### fragment: self-audit" in content
    assert "### fragment: ask-policy" in content
    assert "fragment: fresh-eyes" not in content          # toggled off
    assert "materialized_from" in content
    assert lint_materialized_text(content) == []


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
    d = scaffold(server, slug="papers-radar", name="Papers radar",
                 instruction="# Instruction\n\nCollect papers weekly.",
                 workflow_slug="general-task", cron="0 8 * * 1")
    cfg, problems = load_routine(d)
    assert cfg is not None and problems == [], problems
    assert cfg.cron == "0 8 * * 1" and cfg.workflow_slug == "general-task"
    assert (d / ".git").is_dir()
    assert (d / ".git" / "hooks" / "post-commit").stat().st_mode & 0o111
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["budgets"]["max_turns"] == 60 and raw["self"]["audit"] is True
    assert lint_materialized_text((d / "workflow.md").read_text()) == []
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
    d = scaffold(server, slug="split-routine", name="Split",
                 instruction="# Entry\n\nSteps in playbook/.", workflow_slug="general-task",
                 playbook={"discover": "# Discover step\n\nHow to discover.",
                           "compose.md": "# Compose step\n\nHow to compose."})
    assert (d / "playbook" / "discover.md").read_text().startswith("# Discover step")
    assert (d / "playbook" / "compose.md").read_text().startswith("# Compose step")
